"""Application object: owns the database, library, player, scanner and services."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk  # noqa: E402

from .art import SLIDE_SECONDS_DEFAULT, ArtCache  # noqa: E402
from .config import APP_ID, APP_NAME, ICONS_DIR, MUSIC_DIR, PACKAGE_DIR, VERSION  # noqa: E402
from .database import Database  # noqa: E402
from .library import Library  # noqa: E402
from .lyrics import LyricsService  # noqa: E402
from .mpris import MprisService  # noqa: E402
from .player import Player, Repeat  # noqa: E402
from .scanner import Scanner  # noqa: E402
from .stats import ListeningRecorder  # noqa: E402
from .themes import ThemeManager  # noqa: E402
from .ui.mode_icon import follow_mode  # noqa: E402
from .ui.window import MainWindow  # noqa: E402
from .watcher import LibraryWatcher  # noqa: E402

# Entering Black Hole mode: triple-click the About icon, then type this within the time limit.
BLACK_HOLE_KEYS = "wasd"
BLACK_HOLE_SECONDS = 5

SHORTCUTS = [
    ("Playback", [
        ("Play / Pause", "space"),
        ("Next Song", "<Control>Right"),
        ("Previous Song", "<Control>Left"),
        ("Toggle Shuffle", "<Control>s"),
        ("Cycle Repeat Mode", "<Control>r"),
        ("Favorite Current Song", "<Control>d"),
    ]),
    ("General", [
        ("Search", "<Control>f"),
        ("Switch Player / Queue", "<Control>n"),
        ("Show / Hide Lyrics", "<Control>l"),
        ("Show / Hide Equalizer", "<Control>e"),
        ("Focus on Now Playing", "<Control><Shift>f"),
        ("Hide / Show Interface", "<Control>h"),
        ("Rescan Library", "F5"),
        ("Keyboard Shortcuts", "<Control>question"),
        ("Quit", "<Control>q"),
    ]),
]
# Listed in the Keyboard Shortcuts dialog only while in Black Hole mode.
SLIDESHOW_SHORTCUTS = ("Black Hole Slideshows", [
    ("Repeat Current Slide (on / off)", "<Control><Shift>r"),
    ("Next Slide (press twice)", "Right"),
    ("Previous Slide (press twice)", "Left"),
    ("Next Slideshow in Group", "<Control>bracketright"),
    ("Previous Slideshow in Group", "<Control>bracketleft"),
    ("Next Group", "<Control><Shift>bracketright"),
    ("Previous Group", "<Control><Shift>bracketleft"),
    ("Show the Whole Group", "<Control>g"),
    ("Show the Global Slideshow", "<Control><Shift>g"),
])


class MusicApp(Adw.Application):
    """The application: creates the shared services at startup (database, library, player, scanner, folder
    watcher, cover art, lyrics, themes, MPRIS) and the main window on first activation, and owns the
    app-wide actions and keyboard accelerators.
    Closing the window only hides it (the music keeps playing); Quit ends the app. Black Hole mode is
    entered and left here (see set_black_hole) but its state lives in ArtCache.turned."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.music_dir = MUSIC_DIR
        self.window = None
        self._first_scan_done = False
        self._keys_typed = None  # while waiting for BLACK_HOLE_KEYS: how many were typed so far
        self._keys_timeout = 0

    # ---- lifecycle ----

    def do_startup(self):
        """Create the services, load the stylesheet and icons, install actions and restore saved settings."""
        Adw.Application.do_startup(self)
        # The window is dark glass, so keep text light even when the desktop uses light mode.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        Gst.init(None)

        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
        Gtk.Window.set_default_icon_name(APP_ID)
        css = Gtk.CssProvider()
        css.load_from_path(str(PACKAGE_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.db = Database()
        self.themes = ThemeManager(self.db)  # applies the saved colour theme before any window shows
        self.library = Library(self.db)
        self.art = ArtCache()
        self.lyrics = LyricsService()
        self.player = Player()
        # Listening history for the Stats tab; in Black Hole mode, hidden artists aren't recorded.
        self.stats = ListeningRecorder(self.db, self.player,
                                       skip=lambda t: self.art.turned and self.library.is_hidden(t.artist_key))
        # Nothing queued yet: Play plays All Songs (so without hidden artists).
        self.player.default_tracks = lambda: self.library.visible_items
        self.player.eq_lookup = lambda track: self.db.get_track_eq(track.id)
        self.scanner = Scanner(self.db, self.music_dir)
        self.watcher = LibraryWatcher(self.scanner, self.music_dir)
        self.mpris = MprisService(self, self.player, self.art)
        # Song lists highlight the current song through its `playing` property.
        self._playing_item = None
        self.player.connect("track-changed", self._mark_playing)

        self.scanner.connect("started", lambda *_: self._set_status("Checking library…"))
        self.scanner.connect("progress", self._on_scan_progress)
        self.scanner.connect("finished", self._on_scan_finished)

        self._install_actions()
        self._restore_state()

    def do_activate(self):
        """Show the main window (creating it, and starting the first library scan, on first launch)."""
        if self.window is None:
            self.window = MainWindow(self)
            self.window.add_controller(self._black_hole_keys())
            if not self.music_dir.is_dir():
                self.window.toast(f"Music folder not found: {self.music_dir}")
            else:
                self.scanner.scan()
        self.window.present()

    def do_shutdown(self):
        """A real quit: save window and playback settings, then stop every service."""
        # A real quit (menu, Ctrl+Q, MPRIS). Closing the window only hides it, see MainWindow.
        try:
            if self.window is not None:
                self.window.save_window_state()
            self._save_state()
        except Exception as exc:
            print(f"[app] could not save state: {exc}")
        self.player.shutdown()
        self.watcher.stop()
        self.mpris.shutdown()
        self.art.shutdown()
        self.lyrics.shutdown()
        self.stats.shutdown()  # saves the song in progress, so before the database closes
        self.db.close()
        Adw.Application.do_shutdown(self)

    # ---- actions ----

    def _install_actions(self):
        """Register the app actions (playback, favorites, rescan, dialogs, Black Hole slideshow
        switching) and their keyboard accelerators, including the window actions' accelerators."""
        p = self.player
        actions = {
            "play-pause": (p.play_pause, None),
            "next": (p.next, ["<Control>Right"]),
            "previous": (p.previous, ["<Control>Left"]),
            "shuffle": (p.toggle_shuffle, ["<Control>s"]),
            "repeat": (p.cycle_repeat, ["<Control>r"]),
            "favorite-current": (self._favorite_current, ["<Control>d"]),
            "rescan": (lambda: self.scanner.scan(), ["F5"]),
            "shortcuts": (self._show_shortcuts, ["<Control>question"]),
            "about": (self._show_about, None),
            "quit": (self.quit, ["<Control>q"]),
        }
        for name, (callback, accels) in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        # Black Hole slideshow on/off. Only enabled (and so only shown in the menu) in Black Hole mode.
        self.slideshow_action = Gio.SimpleAction.new_stateful("slideshow", None, GLib.Variant("b", True))
        self.slideshow_action.connect("change-state", self._on_slideshow_state)
        self.slideshow_action.set_enabled(False)
        self.add_action(self.slideshow_action)
        # Switching between slideshows, groups and the Global slideshow (Black Hole mode only).
        # The Shift ones are also registered by their shifted key, however GTK reports it.
        ss = self.art.slideshows
        self.slideshow_switch_actions = []
        for name, accels, switch in (
            ("slideshow-next", ["<Control>bracketright"], lambda: ss.step_slideshow(+1)),
            ("slideshow-previous", ["<Control>bracketleft"], lambda: ss.step_slideshow(-1)),
            ("slideshow-next-group", ["<Control><Shift>bracketright", "<Control>braceright"],
             lambda: ss.step_group(+1)),
            ("slideshow-previous-group", ["<Control><Shift>bracketleft", "<Control>braceleft"],
             lambda: ss.step_group(-1)),
            ("slideshow-group", ["<Control>g"], ss.show_current_group),
            ("slideshow-global", ["<Control><Shift>g"], lambda: ss.select_global() or True),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, switch=switch: self._switch_slideshow(switch))
            action.set_enabled(False)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)
            self.slideshow_switch_actions.append(action)
        self.art.artist_listeners.append(lambda name: self._sync_black_hole_actions() if name is None else None)
        self.art.slideshow_listeners.append(self._sync_slideshow_action)
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.toggle-queue", ["<Control>n"])
        self.set_accels_for_action("win.toggle-lyrics", ["<Control>l"])
        self.set_accels_for_action("win.toggle-eq", ["<Control>e"])
        self.set_accels_for_action("win.focus-mode", ["<Control><Shift>f"])
        self.set_accels_for_action("win.hide-ui", ["<Control>h"])
        self.set_accels_for_action("win.slideshow-repeat", ["<Control><Shift>r"])

    def _on_slideshow_state(self, action, value):
        """The Black Hole slideshow was switched on/off (menu, switch or shortcut): apply and remember it."""
        self.art.set_slideshow_enabled(value.get_boolean())
        self.db.set_setting("slideshow-enabled", value.get_boolean())

    def _sync_black_hole_actions(self):
        """Enable the slideshow actions only while in Black Hole mode."""
        for action in (self.slideshow_action, *self.slideshow_switch_actions):
            action.set_enabled(self.art.turned)

    def _switch_slideshow(self, switch):
        """Run a slideshow switch (a shortcut or menu pick), turn the slideshow on, and say what
        is showing now."""
        if not self.art.turned:
            return
        if not switch():
            self._toast("No slideshows yet: add them in the Slideshows tab")
            return
        if not self.art.slideshow_enabled:
            self.change_action_state("slideshow", GLib.Variant("b", True))
        empty = "" if self.art.slideshow_images() else " (empty)"
        self._toast(f"{self.art.slideshows.describe_active()}{empty}")

    def _toast(self, text):
        """Show a short message in the main window, if it exists."""
        if self.window is not None:
            self.window.toast(text)

    def _sync_slideshow_action(self):
        """Keep the slideshow on/off action's state in step with ArtCache."""
        if self.slideshow_action.get_state().get_boolean() != self.art.slideshow_enabled:
            self.slideshow_action.set_state(GLib.Variant("b", self.art.slideshow_enabled))

    def _favorite_current(self):
        """Toggle the playing song's favorite heart."""
        if self.player.current is not None:
            self.library.toggle_favorite(self.player.current)

    def _show_shortcuts(self):
        """The Keyboard Shortcuts dialog; the Black Hole slideshow section is listed only in Black Hole mode.
"""
        dialog = Adw.ShortcutsDialog()
        for title, items in SHORTCUTS + ([SLIDESHOW_SHORTCUTS] if self.art.turned else []):
            section = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                section.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dialog.add(section)
        dialog.present(self.window)

    def _show_about(self):
        """The About dialog. Its icon is the hidden way into (and out of) Black Hole mode: triple-click it."""
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Carlos Falconett",
            comments="A music player for your local library",
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self.window)
        # While a dialog is open, its keys don't reach the window's controllers: listen here too.
        about.add_controller(self._black_hole_keys())
        icon = _find_icon(about, APP_ID)  # only exists once the dialog is presented
        if icon is not None:
            follow_mode(icon, self.art)  # Sun, or the Black Hole in Black Hole mode
            # Hidden: triple-clicking the icon starts the way into Black Hole mode (a second set
            # of artist images), or leaves it.
            click = Gtk.GestureClick()
            click.connect("pressed", lambda _g, n, _x, _y: self._on_about_icon_clicks() if n == 3 else None)
            icon.add_controller(click)

    # ---- Black Hole mode ----

    def set_black_hole(self, on: bool):
        """Enter or leave Black Hole mode. Everything that looks different in it (mode icons, artist
        images, slideshow, extra actions) follows ArtCache's artist listeners."""
        self._stop_waiting_for_keys()
        self.art.set_turned(on)  # every mode icon flips via the art listeners

    def _on_about_icon_clicks(self):
        """Triple-click on the About icon: leave Black Hole mode, or start waiting for
        BLACK_HOLE_KEYS to be typed within BLACK_HOLE_SECONDS."""
        if self.art.turned:
            self.set_black_hole(False)
            return
        # Now BLACK_HOLE_KEYS must be typed, in order, within BLACK_HOLE_SECONDS.
        self._stop_waiting_for_keys()
        self._keys_typed = 0
        self._keys_timeout = GLib.timeout_add(BLACK_HOLE_SECONDS * 1000, self._keys_timed_out)

    def _keys_timed_out(self):
        """The time to type BLACK_HOLE_KEYS ran out."""
        self._keys_timeout = 0
        self._keys_typed = None
        return False

    def _stop_waiting_for_keys(self):
        """Stop waiting for BLACK_HOLE_KEYS (entered, wrong key, or timed out)."""
        if self._keys_timeout:
            GLib.source_remove(self._keys_timeout)
            self._keys_timeout = 0
        self._keys_typed = None

    def _black_hole_keys(self) -> Gtk.EventControllerKey:
        """A key controller that watches for BLACK_HOLE_KEYS; added to the main window and the About dialog.
"""
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_black_hole_key)
        return keys

    def _on_black_hole_key(self, _ctrl, keyval, _code, state):
        """While waiting: each correct letter advances; any other key cancels. Completing the word
        enters Black Hole mode."""
        if self._keys_typed is None:
            return False
        if _is_modifier(keyval):
            return False  # e.g. Shift on its own
        char = chr(Gdk.keyval_to_unicode(keyval) or 0).lower()
        mods = state & Gtk.accelerator_get_default_mod_mask() & ~Gdk.ModifierType.SHIFT_MASK
        if mods or char != BLACK_HOLE_KEYS[self._keys_typed]:
            self._stop_waiting_for_keys()  # a wrong key: start over with the triple-click
            return False
        self._keys_typed += 1
        if self._keys_typed == len(BLACK_HOLE_KEYS):
            self.set_black_hole(True)
        return True  # typed letters shouldn't start a search

    # ---- scanning ----

    def _set_status(self, text):
        """Show (or with None, hide) the scan status banner."""
        if self.window:
            self.window.set_scan_status(text)

    def _mark_playing(self, _player, track):
        """Move the `playing` flag (which highlights the song in every list) to the new track."""
        if self._playing_item is not None:
            self._playing_item.playing = False
        self._playing_item = track
        if track is not None:
            track.playing = True

    def _on_scan_progress(self, _scanner, done, total):
        self._set_status(f"Reading tags… {done:,} / {total:,}")

    def _on_scan_finished(self, _scanner, changed, removed):
        """Reload the library if anything changed (with a toast after the first scan), and start the
        folder watcher once the first scan is done."""
        self._set_status(None)
        if changed or removed:
            self.library.reload()
            if self._first_scan_done and self.window:
                parts = []
                if changed:
                    parts.append(f"{changed} added or updated")
                if removed:
                    parts.append(f"{removed} removed")
                self.window.toast("Library updated: " + ", ".join(parts))
        if not self._first_scan_done:
            self._first_scan_done = True
            self.watcher.start()

    # ---- persistence ----

    def _restore_state(self):
        """Apply saved settings: volume, shuffle, repeat and the Black Hole slideshow's options."""
        db = self.db
        self.player.set_volume(db.get_setting("volume", 0.8))
        self.player.set_shuffle(db.get_setting("shuffle", False))
        self.player.set_repeat(Repeat(db.get_setting("repeat", 0)))
        self.art.set_slideshow_enabled(bool(db.get_setting("slideshow-enabled", True)))
        self.art.set_slideshow_seconds(db.get_setting("slideshow-seconds", SLIDE_SECONDS_DEFAULT))
        self.art.slideshow_listeners.append(
            lambda: db.set_setting("slideshow-seconds", self.art.slideshow_seconds)
            if db.get_setting("slideshow-seconds", SLIDE_SECONDS_DEFAULT) != self.art.slideshow_seconds else None)
        # Quitting forgets the song and queue, so each launch starts fresh. Remove the saved
        # queue that older versions kept.
        db.delete_setting("queue")

    def _save_state(self):
        """Save the playback settings that are remembered across launches."""
        db = self.db
        db.set_setting("volume", self.player.volume)
        db.set_setting("shuffle", self.player.shuffle)
        db.set_setting("repeat", int(self.player.repeat))


def main(argv=None) -> int:
    """Entry point: run the application with the given command-line arguments."""
    import sys
    app = MusicApp()
    return app.run(argv if argv is not None else sys.argv)


def _is_modifier(keyval) -> bool:
    """Whether a key is a modifier on its own (Shift, Ctrl, …), which the Black Hole word ignores."""
    name = Gdk.keyval_name(keyval) or ""
    return name.split("_")[0] in ("Shift", "Control", "Alt", "Meta", "Super", "Hyper", "Caps", "ISO", "Num")


def _find_icon(widget, icon_name):
    """The first Gtk.Image showing `icon_name` inside `widget` (e.g. the About dialog's app icon)."""
    if isinstance(widget, Gtk.Image) and widget.get_icon_name() == icon_name:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_icon(child, icon_name)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None
