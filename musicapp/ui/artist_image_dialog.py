"""Dialog for choosing a custom picture for any artist. In Black Hole mode it manages
the separate set of Black Hole images (artists without one keep showing their normal image).
Black Hole mode's background slideshows have their own tab (slideshows_view.py)."""

from gi.repository import Adw, Gio, GLib, Gtk

from ..art import THUMB
from .artists_view import ArtistItem


class ArtistImageDialog(Adw.Dialog):
    """The Artist Images dialog: a searchable list of artists, each opening a page to choose or reset
    their picture. In Black Hole mode it edits the Black Hole images instead."""

    def __init__(self, win, artist: str | None = None):
        super().__init__(title="Artist Images", content_width=440, content_height=620)
        self.win = win
        self.art = win.art
        self._terms: list[str] = []
        self._detail: ArtistDetailPage | None = None

        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_child(self.toasts)

        # ---- artist list ----
        self.store = Gio.ListStore(item_type=ArtistItem)
        self.filter = Gtk.CustomFilter.new(lambda item: all(t in item.name.casefold() for t in self._terms))
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        listview = Gtk.ListView(model=Gtk.NoSelection(model=filtered), factory=factory, single_click_activate=True)
        listview.add_css_class("navigation-sidebar")
        listview.connect("activate", lambda lv, pos: self.show_artist(filtered.get_item(pos).name))

        search = Gtk.SearchEntry(placeholder_text="Search artists", margin_start=12, margin_end=12, margin_bottom=6)
        search.connect("search-changed", self._on_search)
        self.list_title = Adw.WindowTitle(title="Artist Images")
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar(title_widget=self.list_title))
        toolbar.add_top_bar(search)
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=listview, vexpand=True))
        self.list_page = Adw.NavigationPage(title="Artist Images", tag="list", child=toolbar)
        self.nav.add(self.list_page)
        self._reload()
        self._sync_mode()

        self._listener = self._on_artist_changed
        self.art.artist_listeners.append(self._listener)
        self.connect("closed", lambda *_: self._on_closed())

        if artist:
            self.show_artist(artist, animate=False)

    def _sync_mode(self):
        """Show whether the dialog edits Black Hole images."""
        turned = self.art.turned
        self.list_title.set_subtitle("Black Hole mode" if turned else "")
        (self.add_css_class if turned else self.remove_css_class)("black-hole-mode")

    def _on_closed(self):
        """Stop listening for changes."""
        self.art.artist_listeners.remove(self._listener)

    # ---- list ----

    def _reload(self):
        """Rebuild the artist list."""
        self.store.splice(0, self.store.get_n_items(),
                          [ArtistItem(n, t) for n, t in self.win.library.artists()])

    def _on_search(self, entry):
        """Filter the artist list by the search words."""
        self._terms = entry.get_text().casefold().split()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _setup_row(self, _f, li):
        """An artist row: avatar, name and detail."""
        row = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4, margin_start=4)
        avatar = Adw.Avatar(size=44, show_initials=True)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        name = Gtk.Label(xalign=0, ellipsize=3)
        detail = Gtk.Label(xalign=0)
        detail.add_css_class("dim")
        detail.add_css_class("caption")
        labels.append(name)
        labels.append(detail)
        row.append(avatar)
        row.append(labels)
        row.append(Gtk.Image(icon_name="go-next-symbolic", margin_end=6))
        row._widgets = (avatar, name, detail)
        li.set_child(row)

    def _bind_row(self, _f, li):
        """Fill an artist row; the detail says whether they have a custom image."""
        row, item = li.get_child(), li.get_item()
        avatar, name, detail = row._widgets
        row._name = item.name
        avatar.set_text(item.name)
        avatar.set_custom_image(None)
        name.set_text(item.name)
        custom = self.art.custom_artist_image(item.name) is not None
        n = len(item.tracks)
        label = "Black Hole image" if self.art.turned else "Custom image"
        detail.set_text(label if custom else f"{n} song{'s' if n != 1 else ''}")

        def apply(texture, token=item.name):
            """Show the avatar, unless the row now shows another artist."""
            if row._name == token:
                avatar.set_custom_image(texture)

        self.art.request_artist(item.name, item.tracks[0] if item.tracks else None, THUMB, apply)

    # ---- detail ----

    def show_artist(self, name: str, animate: bool = True):
        """Open an artist's page."""
        tracks = self.win.library.artist_tracks(name)
        self._detail = ArtistDetailPage(self, name, tracks)
        if animate:
            self.nav.push(self._detail)
        else:
            self.nav.replace([self.list_page, self._detail])

    def _on_artist_changed(self, name):
        """An artist image changed or Black Hole mode switched: refresh the list and the open page."""
        self._reload()  # rebinds rows with fresh avatars and "Custom image" labels
        if name is None:  # Black Hole mode switched
            self._sync_mode()
        if self._detail is not None and name in (None, self._detail.artist):
            self._detail.refresh()

    def toast(self, text):
        """Show a short message inside the dialog."""
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))


class ArtistDetailPage(Adw.NavigationPage):
    """One artist: their current picture, and buttons to choose a new one or go back to the default."""

    def __init__(self, dialog: ArtistImageDialog, artist: str, tracks: list):
        super().__init__(title=artist, tag=f"artist:{artist}")
        self.dialog = dialog
        self.art = dialog.art
        self.artist = artist
        self.tracks = tracks

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                      margin_start=24, margin_end=24, margin_bottom=24)
        self.mode_label = Gtk.Label(label="Black Hole mode", halign=Gtk.Align.CENTER)
        self.mode_label.add_css_class("black-hole-badge")
        self.avatar = Adw.Avatar(size=180, text=artist, show_initials=True, halign=Gtk.Align.CENTER)
        self.avatar.add_css_class("artist-detail-avatar")
        name = Gtk.Label(label=artist, wrap=True, justify=Gtk.Justification.CENTER)
        name.add_css_class("title-2")
        self.status = Gtk.Label()
        self.status.add_css_class("dim")
        choose = Gtk.Button(child=Adw.ButtonContent(icon_name="document-open-symbolic", label="Choose Image…"),
                            halign=Gtk.Align.CENTER, margin_top=12)
        choose.add_css_class("suggested-action")
        choose.add_css_class("pill")
        choose.connect("clicked", lambda *_: self._choose())
        self.reset = Gtk.Button(label="Reset to Default", halign=Gtk.Align.CENTER)
        self.reset.add_css_class("pill")
        self.reset.connect("clicked", lambda *_: self._reset())
        for w in (self.mode_label, self.avatar, name, self.status, choose, self.reset):
            box.append(w)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box))
        self.set_child(toolbar)
        self.refresh()

    def refresh(self):
        """Show which picture is in use (yours, the normal one, or the default) and reload it."""
        turned = self.art.turned
        custom = self.art.custom_artist_image(self.artist) is not None
        self.mode_label.set_visible(turned)
        if turned:
            normal = self.art.custom_artist_image(self.artist, turned=False) is not None
            self.status.set_text("Using your Black Hole image" if custom else
                                 "Using the normal image" if normal else "Using the default image")
        else:
            self.status.set_text("Using your image" if custom else "Using the default image")
        self.reset.set_sensitive(custom)
        self.avatar.set_custom_image(None)
        self.art.request_artist(self.artist, self.tracks[0] if self.tracks else None, THUMB,
                                lambda tex: self.avatar.set_custom_image(tex))

    def _choose(self):
        """Open a file chooser for an image."""
        images = Gtk.FileFilter(name="Images")
        for mime in ("image/jpeg", "image/png", "image/webp"):
            images.add_mime_type(mime)
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(images)
        chooser = Gtk.FileDialog(title=f"Choose an Image for {self.artist}", filters=filters, default_filter=images)
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            chooser.set_initial_folder(Gio.File.new_for_path(pictures))
        chooser.open(self.dialog.win, None, self._on_chosen)

    def _on_chosen(self, chooser, result):
        """Use the chosen file as the artist's picture (a copy is kept by the app)."""
        try:
            file = chooser.open_finish(result)
        except GLib.Error:
            return  # cancelled
        path = file.get_path()
        if not path:
            self.dialog.toast("Only local files can be used")
            return
        try:
            self.art.set_artist_image(self.artist, path)
        except (ValueError, OSError) as exc:
            self.dialog.toast(str(exc))
            return
        mode = " (Black Hole)" if self.art.turned else ""
        self.dialog.toast(f"Updated the image for {self.artist}{mode}")

    def _reset(self):
        """Go back to the default picture (in Black Hole mode: to the normal one)."""
        self.art.reset_artist_image(self.artist)
        if self.art.turned:
            self.dialog.toast(f"Removed the Black Hole image for {self.artist}")
        else:
            self.dialog.toast(f"Restored the default image for {self.artist}")
