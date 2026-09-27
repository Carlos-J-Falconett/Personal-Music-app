"""Main window: top bar with tabs and search, content, right Now Playing panel, player bar."""

import time

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk  # noqa: E402

from ..config import APP_NAME
from .artist_image_dialog import ArtistImageDialog
from .artists_view import ArtistsView
from .backdrop import Backdrop
from .menus import AppMenu, menu_header
from .mode_icon import ModeIcon
from .dialogs import ask_playlist_name, confirm_delete_playlist
from .focus_view import FocusView
from .theme_dialog import ThemeDialog
from .now_playing import NowPlayingPanel
from .playlists_view import PlaylistsView
from .search_view import SearchView
from .slideshows_view import SlideshowsView
from .song_list import SongList
from .stats_view import StatsView
from .widgets import NarrowColumn

# "slideshows" is shown only in Black Hole mode.
TABS = ("songs", "artists", "favorites", "playlists", "stats", "slideshows")
# Icon-only top bar buttons on small windows: the tabs, then Now Playing.
TAB_ICONS = (("songs", "audio-x-generic-symbolic", "All Songs"),
             ("artists", "avatar-default-symbolic", "Artists"),
             ("favorites", "musicapp-heart-filled-symbolic", "Favorites"),
             ("playlists", "musicapp-playlist-symbolic", "Playlists"),
             ("stats", "musicapp-stats-symbolic", "Stats"),
             ("slideshows", "image-x-generic-symbolic", "Slideshows"),
             ("now", "audio-speakers-symbolic", "Now Playing"))
PANEL_MIN_WIDTH = 400
PANEL_DEFAULT_WIDTH = 485
CONTENT_MIN_WIDTH = 440  # the library side never gets narrower than this while resizing
PANEL_CHROME = 38  # the resize handle and the cards' outer margins
PHONE_WIDTH = "max-width: 560sp"
DOUBLE_PRESS_S = 0.5  # Black Hole slideshow: the second arrow press must follow within this
# Black Hole slideshow shortcuts with Shift, matched here by key and Shift: as plain accelerators,
# GTK could take Ctrl+Shift+R for Ctrl+R (Shift is "used up" by the capital letter), and so on.
SHIFT_SHORTCUTS = {
    Gdk.KEY_R: "win.slideshow-repeat", Gdk.KEY_r: "win.slideshow-repeat",
    Gdk.KEY_G: "app.slideshow-global", Gdk.KEY_g: "app.slideshow-global",
    Gdk.KEY_braceright: "app.slideshow-next-group", Gdk.KEY_bracketright: "app.slideshow-next-group",
    Gdk.KEY_braceleft: "app.slideshow-previous-group", Gdk.KEY_bracketleft: "app.slideshow-previous-group",
}
ARROWS = {Gdk.KEY_Right: 1, Gdk.KEY_KP_Right: 1, Gdk.KEY_Left: -1, Gdk.KEY_KP_Left: -1}


class MainWindow(Adw.ApplicationWindow):
    """The main window. Layers, back to front: the artist backdrop (or Black Hole slideshow), then the
    header bar with the tabs (All Songs, Artists, Favorites, Playlists, Stats, and in Black Hole mode
    Slideshows) and search, the library pages, and the Now Playing panel on the right (a full-window
    overlay on small windows); or, instead of all that, the full-window focus view.
    It adapts to three sizes (compact levels 0-2, see the breakpoints in _build), owns the window
    actions (playlists, panels, focus view, Hide Interface, Black Hole's repeat-slide) and the
    window-wide keys (space, Escape, Black Hole slideshow keys)."""
    __gsignals__ = {
        # 0 = wide, 1 = narrow (tabs at bottom), 2 = phone-sized
        "compact-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.library = app.library
        self.player = app.player
        self.art = app.art
        self.compact_level = 0
        self._syncing_icons = False
        self._last_arrow = (0, 0.0)  # (direction, when) of the last slideshow arrow press

        db = self.library.db
        self.set_default_size(db.get_setting("window-width", 1280), db.get_setting("window-height", 800))
        if db.get_setting("window-maximized", False):
            self.maximize()
        self.set_size_request(400, 480)

        self._install_actions()
        self._build()
        self._sync_focus_action()
        self._connect_model()
        self.refresh_all()

        last = db.get_setting("last-tab", "songs")
        # The app starts in normal mode, where the Slideshows tab doesn't exist.
        self.view_stack.set_visible_child_name(last if last in TABS and last != "slideshows" else "songs")

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)

    # ---- layout ----

    def _build(self):
        """Create every part of the window and the adaptive breakpoints."""
        # Tab pages
        self.songs_page = SongList(self, title="All Songs", empty_icon="folder-music-symbolic",
                                   empty_title="No Music Found",
                                   empty_description=f"Add songs to {self.app.music_dir} and they'll appear here")
        self.artists_page = ArtistsView(self)
        self.favorites_page = SongList(self, title="Favorites", empty_icon="musicapp-heart-outline-symbolic",
                                       empty_title="No Favorites Yet",
                                       empty_description="Click the heart next to any song to add it here")
        self.playlists_page = PlaylistsView(self)

        self.view_stack = Adw.ViewStack()
        # Song lists are narrow columns; the space beside them stays empty (backdrop shows).
        self.view_stack.add_titled_with_icon(NarrowColumn(self.songs_page), "songs", "All Songs", "audio-x-generic-symbolic")
        self.view_stack.add_titled_with_icon(self.artists_page, "artists", "Artists", "avatar-default-symbolic")
        self.view_stack.add_titled_with_icon(NarrowColumn(self.favorites_page), "favorites", "Favorites",
                                             "musicapp-heart-filled-symbolic")
        self.view_stack.add_titled_with_icon(self.playlists_page, "playlists", "Playlists",
                                             "musicapp-playlist-symbolic")
        self.stats_page = StatsView(self)
        self.view_stack.add_titled_with_icon(self.stats_page, "stats", "Stats", "musicapp-stats-symbolic")
        # Black Hole mode only (see _sync_black_hole): managing its background slideshows.
        self.slideshows_page = SlideshowsView(self)
        self._slideshows_tab = self.view_stack.add_titled_with_icon(self.slideshows_page, "slideshows", "Slideshows",
                                                                     "image-x-generic-symbolic")

        self.search_view = SearchView(self)
        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.content.add_named(self.view_stack, "tabs")
        self.content.add_named(self.search_view, "search")

        # Now Playing panel (with the player controls) on the right, always shown on wide windows.
        # On narrow windows it becomes a full-window overlay that can be dismissed to browse.
        # Its left edge is a drag handle for resizing it.
        self.panel = NowPlayingPanel(self)
        self.panel.set_hexpand(True)
        self.panel_handle = Gtk.Box(width_request=8, cursor=Gdk.Cursor.new_from_name("col-resize"),
                                    tooltip_text="Drag to resize")
        self.panel_handle.add_css_class("panel-handle")
        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_handle_drag)
        drag.connect("drag-end", lambda *_: self.library.db.set_setting("panel-width", self._panel_width))
        self.panel_handle.add_controller(drag)
        sidebar = Gtk.Box()
        sidebar.append(self.panel_handle)
        sidebar.append(self.panel)
        self._panel_width = int(self.library.db.get_setting("panel-width", PANEL_DEFAULT_WIDTH))
        self.split = Adw.OverlaySplitView(content=self.content, sidebar=sidebar,
                                          sidebar_position=Gtk.PackType.END, pin_sidebar=True,
                                          min_sidebar_width=self._panel_width, max_sidebar_width=self._panel_width,
                                          show_sidebar=True)
        self.split.connect("notify::collapsed", self._on_collapsed_changed)

        # Top bar
        header = Adw.HeaderBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search artists, albums, songs",
                                            valign=Gtk.Align.CENTER, width_request=260)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", lambda *_: self._clear_search())
        self.search_entry.set_key_capture_widget(self)
        self.switcher = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        # Small windows: the tabs and Now Playing as icon-only buttons, in place of the switcher.
        self.tab_icons = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.tab_icons.add_css_class("tab-icons")
        self._tab_buttons: dict[str, Gtk.ToggleButton] = {}
        for name, icon, title in TAB_ICONS:
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=title, valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.add_css_class("tab-icon")
            if self._tab_buttons:
                button.set_group(next(iter(self._tab_buttons.values())))
            button.connect("toggled", self._on_tab_icon, name)
            self.tab_icons.append(button)
            self._tab_buttons[name] = button
        self.title_stack = Gtk.Stack(hhomogeneous=False, interpolate_size=False)
        self.title_stack.add_named(self.switcher, "tabs")
        self.title_stack.add_named(self.tab_icons, "icons")
        header.set_title_widget(self.title_stack)
        # Menu on the left, search on the right.
        self.app_menu = AppMenu(width=280)
        self.app_menu.connect("show", lambda *_: self._fill_app_menu())
        menu_button = Gtk.MenuButton(child=ModeIcon(self.art), tooltip_text="Main Menu",
                                     popover=self.app_menu, primary=True)
        # Black Hole mode: double-clicking the icon leaves it. A single click opens the menu once
        # it's clear no second click is coming.
        self._menu_button = menu_button
        self._menu_open_source = 0
        clicks = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        clicks.connect("pressed", self._on_menu_button_pressed)
        menu_button.add_controller(clicks)
        header.pack_start(menu_button)
        self.header = header
        header.pack_end(self.search_entry)
        # Phone-sized windows: the search field moves to a bar under the top bar, opened by this.
        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)",
                                              visible=False)
        header.pack_end(self.search_button)
        self.search_bar = Gtk.SearchBar()
        self.search_button.bind_property("active", self.search_bar, "search-mode-enabled",
                                         GObject.BindingFlags.BIDIRECTIONAL)

        self.banner = Adw.Banner()

        self.ui_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=250)
        self.ui_stack.add_named(self.split, "ui")
        self.ui_stack.add_named(Gtk.Box(), "hidden")
        outer = Adw.ToolbarView(content=self.ui_stack)
        outer.add_top_bar(header)
        outer.add_top_bar(self.search_bar)
        outer.add_top_bar(self.banner)

        # The normal layout, or the full-window focus view (header bar included, so it hides too).
        self.focus_view = FocusView(self)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=250)
        self.pages.add_named(outer, "main")
        self.pages.add_named(self.focus_view, "focus")
        self.toasts = Adw.ToastOverlay(child=self.pages)
        # Layers: translucent tinted window → artist backdrop → glass panels (see style.css).
        self.add_css_class("main-window")
        self.backdrop = Backdrop(self)
        root = Gtk.Overlay(child=self.backdrop)
        root.add_overlay(self.toasts)
        root.set_measure_overlay(self.toasts, True)  # the UI, not the image, sets the window size
        self.set_content(root)

        # Adaptive layout. Only the last matching breakpoint applies, so each lists all its setters.
        # Level 1 starts where the library would get narrower than its minimum beside the panel
        # (so it follows the panel's width); from then on only Now Playing fills the window.
        self._compact_levels = {}
        self._narrow_breakpoint = None
        for condition, level in ((self._narrow_condition(), 1), (PHONE_WIDTH, 2)):
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(condition))
            bp.add_setter(self.split, "collapsed", True)
            bp.add_setter(self.split, "sidebar-width-fraction", 1.0)
            bp.add_setter(self.split, "max-sidebar-width", 100000.0)
            bp.add_setter(self.split, "min-sidebar-width", 0.0)
            bp.add_setter(self.panel_handle, "visible", False)
            bp.add_setter(self.title_stack, "visible-child-name", "icons")
            bp.add_setter(self.search_entry, "width-request", 200)  # leaves the top bar room to fit
            self._compact_levels[bp] = level
            if level == 1:
                self._narrow_breakpoint = bp
            self.add_breakpoint(bp)
        self.connect("notify::current-breakpoint", self._on_breakpoint)
        # Picking a tab leaves search results (and, on narrow windows, the full-window panel).
        self.view_stack.connect("notify::visible-child", lambda *_: (self.show_ui(), self._clear_search(),
                                                                    self._hide_overlay_panel(),
                                                                    self._sync_tab_icons()))
        self.split.connect("notify::show-sidebar", lambda *_: (self._sync_tab_icons(), self._sync_content()))
        self._sync_tab_icons()
        # Using the tabs (even the one already picked) brings a hidden interface back.
        for bar in (self.switcher, self.tab_icons):
            click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", lambda *_: self.show_ui())
            bar.add_controller(click)
        self._sync_black_hole()

    def _fill_app_menu(self):
        """The app-icon menu, rebuilt each time it opens (its header and Black Hole options change)."""
        page = self.app_menu.page
        page.clear()
        turned = self.art.turned
        songs, playlists = len(self.library.items), len(self.library.playlists())
        page.add_header(menu_header(ModeIcon(self.art, 40), "Black Hole" if turned else APP_NAME,
                                    f"{songs:,} song{'s' if songs != 1 else ''} · "
                                    f"{playlists} playlist{'s' if playlists != 1 else ''}"))
        act = self.activate_action
        page.add_section("Library")
        page.add_item("list-add-symbolic", "New Playlist…", lambda: act("win.new-playlist", None))
        page.add_item("view-refresh-symbolic", "Rescan Library", lambda: act("app.rescan", None), accel="F5")
        page.add_section("Appearance")
        page.add_item("applications-graphics-symbolic", "Themes…", lambda: act("win.themes", None))
        page.add_item("image-x-generic-symbolic", "Artist Images…", lambda: act("win.artist-images", None))
        page.add_switch("view-conceal-symbolic", "Hide Interface", self.ui_hidden, self.set_ui_hidden,
                        accel="<Control>h")
        if turned:
            page.add_section("Black Hole")
            page.add_switch("media-playlist-repeat-symbolic", "Slideshow", self.art.slideshow_enabled,
                            lambda on: self.app.change_action_state("slideshow", GLib.Variant("b", on)))
            page.add_submenu("view-grid-symbolic", "Choose Slideshow", self._fill_slideshow_menu)
            page.add_switch("media-playlist-repeat-song-symbolic", "Repeat Current Slide", self.backdrop.repeating,
                            self.set_slide_repeat, accel="<Control><Shift>r")
            page.add_item("weather-clear-symbolic", "Exit Black Hole Mode", lambda: self.app.set_black_hole(False))
        page.add_section()
        page.add_item("preferences-desktop-keyboard-shortcuts-symbolic", "Keyboard Shortcuts",
                      lambda: act("app.shortcuts", None), accel="<Control>question")
        page.add_item("help-about-symbolic", f"About {APP_NAME}", lambda: act("app.about", None))
        page.add_section()
        page.add_item("application-exit-symbolic", "Quit", lambda: act("app.quit", None), accel="<Control>q",
                      destructive=True)

    def _fill_slideshow_menu(self, page):
        """Black Hole mode: Global, and each group with its slideshows; the showing one is marked."""
        ss = self.art.slideshows
        switch = self.app._switch_slideshow

        def item(rows, icon, label, target, select):
            """A menu row that shows this target, marked when it's the one showing."""
            rows.add_item(icon, label, lambda: switch(lambda: select() or True),
                          detail="Showing" if ss.is_showing(target) else None)

        rows = page.add_scrolling(360)
        item(rows, "view-grid-symbolic", "Global (Everything)", None, ss.select_global)
        for group in ss.groups:
            rows.add_section(group.name)
            item(rows, "view-grid-symbolic", "Whole Group", group, lambda g=group: ss.select_group(g))
            for slideshow in group.slideshows:
                item(rows, "image-x-generic-symbolic", slideshow.name, slideshow,
                     lambda s=slideshow: ss.select_slideshow(s))
        if not ss.groups:
            rows.add_note("image-x-generic-symbolic", "No slideshows yet")
        page.add_section()
        page.add_item("document-edit-symbolic", "Manage Slideshows…", self.show_slideshows)

    def _on_menu_button_pressed(self, gesture, n_press, _x, _y):
        """Black Hole mode: a double-click on the menu icon leaves the mode; a single click opens the
        menu once it's clear no second click is coming."""
        if not self.art.turned or self.app_menu.get_visible():
            return  # the usual menu button (a click with the menu open closes it)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._menu_open_source:
            GLib.source_remove(self._menu_open_source)
            self._menu_open_source = 0
        if n_press >= 2:
            self.app.set_black_hole(False)
            return

        def open_menu():
            self._menu_open_source = 0
            self._menu_button.popup()
            return False
        delay = Gtk.Settings.get_default().get_property("gtk-double-click-time")
        self._menu_open_source = GLib.timeout_add(delay, open_menu)

    def _on_breakpoint(self, *_):
        """The window crossed a size breakpoint: move the search field, restyle, tell listeners."""
        level = self._compact_levels.get(self.get_current_breakpoint(), 0)
        if level == 0:
            # Leaving narrow mode: Adw restores the width the panel had when it was built,
            # so put back the one the user dragged to.
            GLib.idle_add(self._apply_panel_width)
        if level != self.compact_level:
            self._place_search(phone=level == 2)
            # Phone-sized: tighter buttons and headers (style.css), so everything still fits.
            (self.add_css_class if level == 2 else self.remove_css_class)("phone")
            self.compact_level = level
            self.emit("compact-changed", level)

    def _narrow_condition(self) -> str:
        """The breakpoint below which the library no longer fits beside the Now Playing panel."""
        return f"max-width: {self._panel_width + CONTENT_MIN_WIDTH + PANEL_CHROME}px"

    def _place_search(self, phone: bool):
        """Phone-sized windows: the search field lives in a bar under the top bar (opened by the
        search button or by typing); otherwise at the end of the top bar."""
        in_bar = self.search_entry.get_parent() is not self.header and self.search_bar.get_child() is not None
        if phone == in_bar:
            return
        capture = self.search_entry.get_key_capture_widget() or self.search_bar.get_key_capture_widget()
        self.search_entry.set_key_capture_widget(None)
        self.search_bar.set_key_capture_widget(None)
        if phone:
            self.header.remove(self.search_entry)
            self.search_entry.set_hexpand(True)
            self.search_bar.set_child(self.search_entry)
            self.search_bar.connect_entry(self.search_entry)
            self.search_bar.set_key_capture_widget(capture)
            self.search_bar.set_search_mode(bool(self.search_entry.get_text()))
        else:
            self.search_bar.set_search_mode(False)
            self.search_bar.set_child(None)
            self.search_entry.set_hexpand(False)
            self.header.pack_end(self.search_entry)
            self.search_entry.set_key_capture_widget(capture)
        self.search_button.set_visible(phone)

    def _set_search_capture(self, on: bool):
        """Whether typing anywhere in the window starts a search."""
        target = self if on else None
        if self.search_bar.get_child() is not None:
            self.search_bar.set_key_capture_widget(target)
        else:
            self.search_entry.set_key_capture_widget(target)

    # ---- top bar icons (small windows) ----

    def _on_tab_icon(self, button, name):
        """Small windows: a top-bar icon was picked (a tab, or Now Playing)."""
        if self._syncing_icons or not button.get_active():
            return
        self.show_ui()
        if name == "now":
            self.split.set_show_sidebar(True)
        else:
            self._clear_search()
            self.view_stack.set_visible_child_name(name)
            self._hide_overlay_panel()

    def _sync_tab_icons(self):
        """Keep the top-bar icons in step with what's showing."""
        showing_panel = self.split.get_collapsed() and self.split.get_show_sidebar()
        name = "now" if showing_panel else self.view_stack.get_visible_child_name()
        button = self._tab_buttons.get(name)
        if button is not None and not button.get_active():
            self._syncing_icons = True
            button.set_active(True)
            self._syncing_icons = False

    # ---- Now Playing panel ----

    def _on_handle_drag(self, gesture, _dx, _dy):
        """Dragging the Now Playing panel's left edge resizes it."""
        # Use the pointer's window position (not the drag offset): the handle itself moves as
        # the panel resizes, so widget-relative offsets would feed back on themselves.
        ok, x, y = gesture.get_point(None)
        if not ok:
            return
        ok, point = self.panel_handle.compute_point(self.get_content(), Graphene.Point().init(x, y))
        if ok:
            self.set_panel_width(self.get_content().get_width() - point.x)

    def set_panel_width(self, width: float):
        """Set the Now Playing panel's width, keeping the library at least CONTENT_MIN_WIDTH wide."""
        limit = max(PANEL_MIN_WIDTH, self.get_width() - CONTENT_MIN_WIDTH - PANEL_CHROME)
        self._panel_width = int(min(max(width, PANEL_MIN_WIDTH), limit))
        self._apply_panel_width()
        # The library gives way first: only Now Playing is left once it would get too narrow.
        self._narrow_breakpoint.set_condition(Adw.BreakpointCondition.parse(self._narrow_condition()))

    def _apply_panel_width(self):
        """Apply the chosen panel width (docked panel only)."""
        if not self.split.get_collapsed():
            self.split.set_min_sidebar_width(self._panel_width)
            self.split.set_max_sidebar_width(self._panel_width)
        return False

    def show_library(self):
        """Narrow windows: dismiss the full-window panel to browse the library."""
        self.split.set_show_sidebar(False)

    def _on_collapsed_changed(self, split, _pspec):
        """Switching between the docked panel and the window-filling one."""
        collapsed = split.get_collapsed()
        split.set_show_sidebar(True)  # docked: always shown; narrow: start on the player
        self.panel.set_collapsed(collapsed)
        self._sync_tab_icons()
        self._sync_content()

    def _sync_content(self):
        """While Now Playing fills a small window, the library underneath is hidden, so the
        artist backdrop shows through the (card-less) panel."""
        covered = self.split.get_collapsed() and self.split.get_show_sidebar()
        self.content.set_opacity(0.0 if covered else 1.0)
        self.content.set_can_target(not covered)

    def _hide_overlay_panel(self):
        """Small windows: dismiss the full-window Now Playing panel."""
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    # ---- actions ----

    def _install_actions(self):
        """Register the window actions (win.*). Their accelerators are set in MusicApp._install_actions."""
        def add(name, callback, param=None):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            action.connect("activate", callback)
            self.add_action(action)

        add("new-playlist", lambda *_: self.new_playlist_with([]))
        add("rename-playlist", lambda _a, p: self.rename_playlist(p.unpack()), "i")
        add("delete-playlist", lambda _a, p: self.delete_playlist(p.unpack()), "i")
        add("play-playlist", lambda _a, p: self._play_playlist(p.unpack()), "i")
        add("toggle-queue", lambda *_: self._toggle_queue())
        add("search", lambda *_: self._focus_search())
        add("toggle-lyrics", lambda *_: self._toggle_lyrics())
        add("toggle-eq", lambda *_: self._toggle_eq())
        add("artist-images", lambda *_: self.show_artist_images())
        add("themes", lambda *_: ThemeDialog(self).present(self))
        add("focus-mode", lambda *_: self.set_focus_mode(not self.focus_mode))
        add("hide-ui", lambda *_: self.set_ui_hidden(not self.ui_hidden))
        add("slideshow-repeat", lambda *_: self.set_slide_repeat(not self.backdrop.repeating))
        self.lookup_action("slideshow-repeat").set_enabled(False)  # Black Hole mode only
        # Focus view needs a song; it can always be left again.
        self.player.connect("track-changed", lambda *_: self._sync_focus_action())

    # ---- focus view ----

    @property
    def focus_mode(self) -> bool:
        """Whether the full-window focus view is showing."""
        return self.pages.get_visible_child_name() == "focus"

    def set_focus_mode(self, on: bool):
        """Show or leave the focus view (it needs a song to show)."""
        if on == self.focus_mode:
            return
        if on and self.player.current is None:
            return
        if not on:
            self.focus_view.close_popup()
        self.pages.set_visible_child_name("focus" if on else "main")
        # Typing shouldn't start a search in the hidden header.
        self._set_search_capture(not on)
        self._sync_focus_action()

    def _sync_focus_action(self):
        """The focus view can be opened only when there's a song."""
        self.lookup_action("focus-mode").set_enabled(self.player.current is not None or self.focus_mode)

    # ---- hidden interface (Hide Interface, Ctrl+H) ----

    @property
    def ui_hidden(self) -> bool:
        """Whether the interface is hidden (Hide Interface), leaving only the background."""
        return self.ui_stack.get_visible_child_name() == "hidden"

    def set_ui_hidden(self, hidden: bool):
        """Hide everything under the top bar, or bring it back. Using the tabs also brings it back."""
        if hidden != self.ui_hidden:
            self.ui_stack.set_visible_child_name("hidden" if hidden else "ui")

    def show_ui(self):
        """Bring a hidden interface back."""
        if self.ui_hidden:
            self.set_ui_hidden(False)

    def set_slide_repeat(self, on: bool):
        """Black Hole slideshow: keep showing the current slide, or carry on."""
        if not self.art.turned or on == self.backdrop.repeating:
            return
        self.backdrop.set_repeat(on)
        self.toast("Repeating this slide" if on else "Slideshow continues")

    def _sync_black_hole(self):
        """Black Hole mode switched: its window action and the Slideshows tab (and its small-window
        icon) appear only in it. Leaving the mode while on that tab goes to All Songs."""
        turned = self.art.turned
        self.lookup_action("slideshow-repeat").set_enabled(turned)
        self._slideshows_tab.set_visible(turned)
        self._tab_buttons["slideshows"].set_visible(turned)
        if not turned and self.view_stack.get_visible_child_name() == "slideshows":
            self.view_stack.set_visible_child_name("songs")

    def _toggle_queue(self):
        """Show/hide the queue, in the focus view's popup or the Now Playing panel."""
        self.show_ui()
        if self.focus_mode:
            self.focus_view.toggle("queue")
            return
        self.split.set_show_sidebar(True)
        self.panel.toggle_queue()

    def _toggle_lyrics(self):
        """Show/hide the lyrics, in the focus view's popup or on the Now Playing card."""
        self.show_ui()
        if self.focus_mode:
            self.focus_view.toggle("lyrics")
            return
        self.split.set_show_sidebar(True)
        self.panel.toggle_lyrics()

    def _toggle_eq(self):
        """Show/hide the equalizer, in the focus view's popup or on the Now Playing card."""
        self.show_ui()
        if self.focus_mode:
            self.focus_view.toggle("eq")
            return
        self.split.set_show_sidebar(True)
        self.panel.toggle_eq()

    def show_artist_images(self, artist: str | None = None):
        """The Artist Images dialog, optionally open on one artist."""
        ArtistImageDialog(self, artist).present(self)

    def show_slideshows(self):
        """Black Hole mode: go to the Slideshows tab."""
        if not self.art.turned:
            return
        self.show_ui()
        self._clear_search()
        self.view_stack.set_visible_child_name("slideshows")
        self._hide_overlay_panel()

    def _on_artist_image_changed(self, name):
        """An artist image changed (or Black Hole mode switched): refresh what shows artist images."""
        if name is None:  # Black Hole mode switched
            self._sync_black_hole()
        self.artists_page.refresh()
        self.search_view.refresh()

    def _focus_search(self):
        """Ctrl+F: focus the search field (opening its bar on phone-sized windows)."""
        if self.search_bar.get_child() is not None:
            self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def _on_key_pressed(self, _ctrl, keyval, _code, state):
        """Window-wide keys, before any widget sees them: Black Hole slideshow keys, space for
        play/pause (unless typing), Escape to leave the focus view or clear the search."""
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if self.art.turned and self._slideshow_key(keyval, mods):
            return True
        if keyval == Gdk.KEY_space and not mods:
            if isinstance(self.get_focus(), (Gtk.Text, Gtk.Editable)):
                return False
            self.player.play_pause()
            return True
        if keyval == Gdk.KEY_Escape and self.focus_mode and not mods:
            # First close an open popup, then leave the focus view.
            if not self.focus_view.close_popup():
                self.set_focus_mode(False)
            return True
        if keyval == Gdk.KEY_Escape and self.search_entry.get_text():
            self._clear_search()
            return True
        return False

    def _slideshow_key(self, keyval, mods) -> bool:
        """Black Hole mode's slideshow keys. True if the key was used."""
        shift_ctrl = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if mods == shift_ctrl and keyval in SHIFT_SHORTCUTS:
            self.activate_action(SHIFT_SHORTCUTS[keyval], None)
            return True
        if mods or keyval not in ARROWS or not self.backdrop.slideshow_playing:
            return False
        if isinstance(self.get_focus(), (Gtk.Text, Gtk.Editable)):
            return False  # typing: the arrows move the cursor
        # Pressed twice: next / previous slide. Once does nothing (the arrows belong to the
        # slideshow in Black Hole mode).
        step, now = ARROWS[keyval], time.monotonic()
        last_step, last_time = self._last_arrow
        if last_step == step and now - last_time <= DOUBLE_PRESS_S:
            self._last_arrow = (0, 0.0)
            self.backdrop.step_slide(step)
        else:
            self._last_arrow = (step, now)
        return True

    # ---- search ----

    def _on_search_changed(self, entry):
        """Typing in the search field shows the results in place of the tabs."""
        text = entry.get_text().strip()
        if text:
            self.show_ui()
            if self.content.get_visible_child_name() != "search":
                self.search_view.reset()
            self.search_view.search(text)
            self.content.set_visible_child_name("search")
            self._hide_overlay_panel()
        else:
            self.content.set_visible_child_name("tabs")

    def _clear_search(self):
        """Empty the search field and go back to the tabs."""
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        self.content.set_visible_child_name("tabs")

    # ---- navigation ----

    def show_artist(self, name: str):
        """Go to an artist's page."""
        self.show_ui()
        self._clear_search()
        self.view_stack.set_visible_child_name("artists")
        self.artists_page.show_artist(name)
        self._hide_overlay_panel()

    def show_album(self, key):
        """Go to an album's page (key: (artist, album title))."""
        self.show_ui()
        album = self.library.find_album(key)
        if album is None:
            return
        self._clear_search()
        self.view_stack.set_visible_child_name("artists")
        self.artists_page.show_album(album)
        self._hide_overlay_panel()

    def show_playlist(self, pid: int):
        """Go to a playlist's page."""
        self.show_ui()
        self._clear_search()
        self.view_stack.set_visible_child_name("playlists")
        self.playlists_page.show_playlist(pid)

    # ---- data sync ----

    def _connect_model(self):
        """Keep the pages in step with library and player changes."""
        self.library.connect("changed", lambda *_: self.refresh_all())
        self.library.connect("favorites-changed", lambda *_: self._refresh_favorites())
        # Adding or removing songs also emits playlists-changed, so this covers contents too.
        self.library.connect("playlists-changed", lambda *_: self.playlists_page.refresh())
        self.player.connect("error", lambda _p, msg: self.toast(msg))
        self.art.artist_listeners.append(self._on_artist_image_changed)

    def refresh_all(self):
        """Reload every page after the library changed, and drop deleted songs from the queue."""
        self.songs_page.set_items(self.library.sorted_items)
        self.artists_page.refresh()
        self._refresh_favorites()
        self.playlists_page.refresh()
        self.search_view.refresh()
        self.player.remove_tracks(lambda t: t.id not in self.library.items)

    def _refresh_favorites(self):
        """Reload the Favorites page."""
        self.favorites_page.set_items(self.library.favorite_items())

    # ---- playlist operations ----

    def new_playlist_with(self, items):
        """Ask for a name and create a playlist, with `items` in it (or empty, then open it)."""
        def create(name):
            pid = self.library.create_playlist(name)
            if items:
                self.library.add_to_playlist(pid, items)
                self.toast(f"Created “{name}” with {len(items)} song{'s' if len(items) != 1 else ''}")
            else:
                self.show_playlist(pid)

        if not items:
            body = "Name your new playlist"
        elif len(items) == 1:
            body = f"With “{items[0].title}”"
        else:
            body = f"With {len(items)} songs"
        ask_playlist_name(self, heading="New Playlist", confirm="Create", body=body, on_done=create)

    def rename_playlist(self, pid):
        """Ask for a playlist's new name."""
        name = self.library.playlist_name(pid)
        if name is None:
            return
        ask_playlist_name(self, heading="Rename Playlist", confirm="Rename", initial=name,
                          body=f"Rename “{name}”",
                          on_done=lambda new: self.library.rename_playlist(pid, new))

    def delete_playlist(self, pid):
        """Confirm, then delete a playlist."""
        name = self.library.playlist_name(pid)
        if name is None:
            return

        def do_delete():
            self.library.delete_playlist(pid)
            self.toast(f"Deleted “{name}”")

        confirm_delete_playlist(self, name, do_delete)

    def _play_playlist(self, pid):
        """Play a playlist from the start."""
        items = self.library.playlist_items(pid)
        if items:
            self.player.play_tracks(items, 0)

    # ---- misc ----

    def toast(self, message: str):
        """Show a short message at the bottom of the window."""
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=3))

    def set_scan_status(self, text):
        """Show the library scan's progress in the banner (None hides it)."""
        if text:
            self.banner.set_title(text)
        self.banner.set_revealed(bool(text))

    def _on_close(self, *_):
        """Closing the window keeps the app (and the music) running in the background; the same
        window comes back on the next launch. Only Quit ends the app and forgets the song."""
        self.save_window_state()
        self.set_visible(False)
        return True

    def save_window_state(self):
        """Remember the window size, the tab and the panel width for the next launch."""
        db = self.library.db
        if not self.is_maximized():
            width, height = self.get_default_size()
            db.set_setting("window-width", width)
            db.set_setting("window-height", height)
        db.set_setting("window-maximized", self.is_maximized())
        db.set_setting("last-tab", self.view_stack.get_visible_child_name())
        db.set_setting("panel-width", self._panel_width)
