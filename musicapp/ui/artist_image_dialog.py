"""Dialog for choosing a custom picture for any artist. In Black Hole mode it manages
the separate set of Black Hole images (artists without one keep showing their normal image)
and the background slideshow, which replaces artist images while Black Hole mode is on."""

from gi.repository import Adw, Gio, GLib, Gtk

from ..art import SLIDE_SECONDS_MAX, SLIDE_SECONDS_MIN, THUMB, is_video
from .artists_view import ArtistItem
from .silent_video import SilentVideo


class ArtistImageDialog(Adw.Dialog):
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
        # Black Hole mode only: the background slideshow.
        self.slideshow_row = Adw.ActionRow(title="Background Slideshow", activatable=True)
        self.slideshow_row.add_css_class("slideshow-card")
        tile = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False)
        tile.add_css_class("icon-tile")
        tile.append(Gtk.Image(icon_name="image-x-generic-symbolic", hexpand=True))
        self.slideshow_row.add_prefix(tile)
        self.slideshow_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.slideshow_row.connect("activated", lambda *_: self.show_slideshow())
        slideshow_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     margin_start=12, margin_end=12, margin_bottom=8)
        slideshow_list.add_css_class("boxed-list")
        slideshow_list.add_css_class("slideshow-entry")
        slideshow_list.append(self.slideshow_row)
        self.slideshow_entry = slideshow_list
        toolbar.add_top_bar(slideshow_list)
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=listview, vexpand=True))
        self.list_page = Adw.NavigationPage(title="Artist Images", tag="list", child=toolbar)
        self.nav.add(self.list_page)
        self._reload()
        self._sync_mode()

        self._listener = self._on_artist_changed
        self._slides_listener = self._sync_slideshow_row
        self.art.artist_listeners.append(self._listener)
        self.art.slideshow_listeners.append(self._slides_listener)
        self.connect("closed", lambda *_: self._on_closed())
        self.nav.connect("popped", lambda _nav, page: page.detach() if isinstance(page, SlideshowPage) else None)

        if artist:
            self.show_artist(artist, animate=False)

    def _sync_mode(self):
        turned = self.art.turned
        self.list_title.set_subtitle("Black Hole mode" if turned else "")
        (self.add_css_class if turned else self.remove_css_class)("black-hole-mode")
        self.slideshow_entry.set_visible(turned)
        self._sync_slideshow_row()
        if not turned and self.nav.find_page("slideshow") is not None:
            self.nav.pop_to_page(self.list_page)  # the slideshow is a Black Hole mode option

    def _on_closed(self):
        self.art.artist_listeners.remove(self._listener)
        self.art.slideshow_listeners.remove(self._slides_listener)
        page = self.nav.find_page("slideshow")
        if page is not None:
            page.detach()

    def _sync_slideshow_row(self):
        n = len(self.art.slideshow_images())
        state = "On" if self.art.slideshow_enabled else "Off"
        self.slideshow_row.set_subtitle(f"{n} image{'s' if n != 1 else ''} · {state}" if n else "Not set")

    def show_slideshow(self):
        if self.art.turned and self.nav.find_page("slideshow") is None:
            self.nav.push(SlideshowPage(self))

    # ---- list ----

    def _reload(self):
        self.store.splice(0, self.store.get_n_items(),
                          [ArtistItem(n, t) for n, t in self.win.library.artists()])

    def _on_search(self, entry):
        self._terms = entry.get_text().casefold().split()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _setup_row(self, _f, li):
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
            if row._name == token:
                avatar.set_custom_image(texture)

        self.art.request_artist(item.name, item.tracks[0] if item.tracks else None, THUMB, apply)

    # ---- detail ----

    def show_artist(self, name: str, animate: bool = True):
        tracks = self.win.library.artist_tracks(name)
        self._detail = ArtistDetailPage(self, name, tracks)
        if animate:
            self.nav.push(self._detail)
        else:
            self.nav.replace([self.list_page, self._detail])

    def _on_artist_changed(self, name):
        self._reload()  # rebinds rows with fresh avatars and "Custom image" labels
        if name is None:  # Black Hole mode switched
            self._sync_mode()
        if self._detail is not None and name in (None, self._detail.artist):
            self._detail.refresh()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))


class ArtistDetailPage(Adw.NavigationPage):
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
        self.art.reset_artist_image(self.artist)
        if self.art.turned:
            self.dialog.toast(f"Removed the Black Hole image for {self.artist}")
        else:
            self.dialog.toast(f"Restored the default image for {self.artist}")


class SlideshowPage(Adw.NavigationPage):
    """Black Hole mode's background slideshow: one set of images and videos shown instead of
    artist images: each image for a chosen number of seconds, each video until it ends."""

    def __init__(self, dialog: ArtistImageDialog):
        super().__init__(title="Background Slideshow", tag="slideshow")
        self.dialog = dialog
        self.art = dialog.art

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        self.caption = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.caption.add_css_class("dim")
        box.append(self.caption)

        self.switch = Adw.SwitchRow(title="Play Slideshow",
                                    subtitle="Off shows artist images again")
        self.switch.set_active(self.art.slideshow_enabled)
        # Goes through app.slideshow, so the menu's Slideshow item and the saved setting follow.
        self.switch.connect("notify::active", lambda row, _p: self.art.slideshow_enabled != row.get_active()
                            and dialog.win.app.change_action_state("slideshow", GLib.Variant("b", row.get_active())))
        self.seconds = Adw.SpinRow(title="Seconds per Image",
                                   subtitle="Videos always play to the end",
                                   adjustment=Gtk.Adjustment(lower=SLIDE_SECONDS_MIN, upper=SLIDE_SECONDS_MAX,
                                                             step_increment=1, page_increment=10,
                                                             value=self.art.slideshow_seconds))
        self.seconds.connect("notify::value", lambda row, _p: self.art.set_slideshow_seconds(int(row.get_value())))
        switch_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        switch_list.add_css_class("boxed-list")
        switch_list.append(self.switch)
        switch_list.append(self.seconds)
        box.append(switch_list)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=2, max_children_per_line=6,
                                column_spacing=10, row_spacing=10, valign=Gtk.Align.START)
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="No Images or Videos",
                                    description="Add a few to start the slideshow", vexpand=True)
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.flow, "images")
        box.append(self.stack)

        # Side by side, or one above the other when the dialog is too narrow for both.
        buttons = Adw.WrapBox(child_spacing=12, line_spacing=10, halign=Gtk.Align.CENTER, margin_top=6)
        add = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Images or Videos…"))
        add.add_css_class("suggested-action")
        add.add_css_class("pill")
        add.connect("clicked", lambda *_: self._choose())
        self.remove_all = Gtk.Button(label="Remove All")
        self.remove_all.add_css_class("pill")
        self.remove_all.connect("clicked", lambda *_: self._confirm_clear())
        buttons.append(add)
        buttons.append(self.remove_all)
        box.append(buttons)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box))
        self.set_child(toolbar)

        self._videos: list[SilentVideo] = []  # the video thumbnails' players
        self._listener = self.refresh
        self.art.slideshow_listeners.append(self._listener)
        self.refresh()

    def detach(self):
        """Stop following slideshow changes (the page was popped or the dialog closed)."""
        if self._listener in self.art.slideshow_listeners:
            self.art.slideshow_listeners.remove(self._listener)
        self._stop_videos()

    def _stop_videos(self):
        for media in self._videos:
            media.stop()
        self._videos = []

    def refresh(self):
        if self.switch.get_active() != self.art.slideshow_enabled:
            self.switch.set_active(self.art.slideshow_enabled)
        seconds = self.art.slideshow_seconds
        if int(self.seconds.get_value()) != seconds:
            self.seconds.set_value(seconds)
        self.caption.set_label(f"Shown instead of artist images while Black Hole mode is on, "
                               f"each image for {seconds} second{'s' if seconds != 1 else ''} "
                               f"and each video to its end.")
        self.flow.remove_all()
        self._stop_videos()
        images = self.art.slideshow_images()
        for path in images:
            self.flow.append(self._thumb(path))
        self.stack.set_visible_child_name("images" if images else "empty")
        self.remove_all.set_sensitive(bool(images))

    def _thumb(self, path):
        # The picture is an overlay so its (wide) natural size doesn't stretch the square thumbnail.
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        overlay = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        overlay.add_css_class("slide-thumb")
        overlay.set_child(Gtk.Box(width_request=110, height_request=110))
        overlay.add_overlay(picture)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove from Slideshow",
                            halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=4, margin_end=4)
        remove.add_css_class("circular")
        remove.add_css_class("slide-remove")
        remove.connect("clicked", lambda *_: self.art.remove_slideshow_image(path))
        overlay.add_overlay(remove)
        if is_video(path):
            # The video's first frame (a paused, silent video shows it once it's ready), plus a film badge.
            media = SilentVideo(path)  # no sound: see silent_video.py
            picture.set_paintable(media.paintable)
            self._videos.append(media)  # stopped on the next refresh, or when the page closes
            badge = Gtk.Image(icon_name="video-x-generic-symbolic", halign=Gtk.Align.START,
                              valign=Gtk.Align.END, margin_start=6, margin_bottom=6)
            badge.add_css_class("slide-video-badge")
            overlay.add_overlay(badge)
            overlay.set_tooltip_text("Video")
        else:
            self.art.request_slide(path, THUMB, lambda texture: picture.set_paintable(texture))
        return overlay

    def _choose(self):
        media = Gtk.FileFilter(name="Images and Videos")
        for mime in ("image/jpeg", "image/png", "image/webp", "video/*"):
            media.add_mime_type(mime)
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(media)
        chooser = Gtk.FileDialog(title="Choose Slideshow Images or Videos", filters=filters, default_filter=media)
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            chooser.set_initial_folder(Gio.File.new_for_path(pictures))
        chooser.open_multiple(self.dialog.win, None, self._on_chosen)

    def _on_chosen(self, chooser, result):
        try:
            files = chooser.open_multiple_finish(result)
        except GLib.Error:
            return  # cancelled
        paths = [f.get_path() for f in files if f.get_path()]
        if not paths:
            return

        def done(added, skipped):
            text = f"Added {added} to the slideshow"
            if skipped:
                text += f" ({skipped} couldn't be read)"
            self.dialog.toast(text)

        if any(is_video(p) for p in paths):
            self.dialog.toast("Adding…")  # videos take a moment to copy
        self.art.add_slideshow_images(paths, done)

    def _confirm_clear(self):
        alert = Adw.AlertDialog(heading="Remove All Images?",
                                body="The slideshow will stop and artist images will show again.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("remove", "Remove All")
        alert.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.connect("response", lambda _a, r: self.art.clear_slideshow() if r == "remove" else None)
        alert.present(self.dialog)
