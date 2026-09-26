"""Colour themes: a named set of hex colours applied over the stylesheet.

The built-in Default theme is the app's normal look (style.css plus the system accent colour);
applying it adds nothing. Custom themes are stored in the settings table and applied through
one CSS provider that overrides the stylesheet's palette variables and libadwaita's colours.

A theme always has the basic colours (COLORS). Every element also has its own detailed colour
(GROUPS), which a theme may leave out: style.css then paints that element as it always has,
following the basic colours (e.g. song titles follow Text). A set detailed colour overrides the
element's stylesheet variable, --<key>-color."""

from dataclasses import dataclass, field

from gi.repository import Adw, Gdk, Gtk

# (key, label, what it paints) in the order the editor lists them.
COLORS = [
    ("accent", "Accent", "Play buttons, active tabs and toggles, sliders, the playing song"),
    ("accent_text", "Accent Text", "Text and icons on accent-coloured buttons"),
    ("text", "Text", "Main text"),
    ("window", "Window Tint", "The tint behind everything"),
    ("panels", "Panels", "The floating cards, popups and bars"),
    ("favorite", "Favorite", "The filled heart"),
    ("wave_top", "Wave Top", "Visualizer upper edge"),
    ("wave_bottom", "Wave Bottom", "Visualizer lower edge"),
]
REQUIRED = [key for key, _label, _desc in COLORS]

# Detailed colours, by area: (key, label, what it paints, follows, alpha). While unset, an element
# looks like `follows` (a key, "accent_light", or a hex) at `alpha` (over the panels): used only to
# show the swatch of what it's following. The real defaults are in style.css.
# Surfaces and lines ("…, kept faint/glassy") take the colour's hue and keep their translucency.
GROUPS = [
    ("General", [
        ("secondary_text", "Secondary Text", "Subtitles, counts, times and other quieter text", "text", 0.58),
        ("borders", "Borders", "Outlines of cards, panels and bars (kept faint)", "text", 1),
        ("highlight", "Hover Highlight", "The tint under hovered rows and buttons (kept faint)", "text", 1),
        ("scrollbars", "Scrollbars", "Scrollbar handles (kept translucent)", "text", 1),
        ("focus_outline", "Keyboard Focus", "The outline around the focused control", "accent_light", 1),
        ("backdrop_dim", "Background Dim", "The shade over the artist backdrop (kept translucent)", "#000000", 1),
    ]),
    ("Header Bar", [
        ("tabs_background", "Tabs Bar", "Behind the All Songs / Artists / Favorites / Playlists tabs", "panels", 1),
        ("tab_text", "Tab Text", "Tabs that aren't selected", "secondary_text", 1),
        ("tab_selected_text", "Selected Tab Text", "The selected (and hovered) tab", "text", 1),
        ("tab_selected", "Selected Tab", "The pill behind the selected tab (kept faint)", "highlight", 1),
        ("search_background", "Search Box", "Behind the search field", "panels", 1),
        ("search_text", "Search Text", "Text typed in the search field", "text", 1),
        ("menu_button", "Menu Button", "The circle behind the Sun / Black Hole icon", "panels", 1),
    ]),
    ("Library Pages", [
        ("library_background", "Page Cards", "The glass cards holding the library pages", "panels", 1),
        ("page_title", "Page Title", "Big titles like “All Songs” or an artist's name", "text", 1),
        ("page_subtitle", "Page Subtitle", "The count under a page title", "secondary_text", 1),
        ("column_headers", "Column Headers", "SONG / DURATION above song lists", "secondary_text", 0.66),
        ("play_all", "Play Button", "The Play pill on pages", "accent", 1),
        ("play_all_text", "Play Button Text", "Its label and icon", "accent_text", 1),
        ("shuffle_all", "Shuffle Button", "The Shuffle pill on pages (kept faint)", "highlight", 1),
        ("shuffle_all_text", "Shuffle Button Text", "Its label and icon", "text", 1),
        ("section_titles", "Search Section Titles", "Artists / Albums / Songs in search results", "text", 1),
    ]),
    ("Song Lists", [
        ("song_title", "Song Title", "Song names in lists", "text", 1),
        ("song_artist", "Song Artist", "The artist under each song name", "secondary_text", 1),
        ("track_number", "Track Number", "The # column on album pages", "secondary_text", 1),
        ("duration", "Duration", "Song lengths in lists and search", "secondary_text", 1),
        ("playing_song", "Playing Song", "The song that's playing, in lists", "accent_light", 1),
        ("row_hover", "Hovered Row", "Under the song the pointer is on (kept faint)", "highlight", 1),
        ("row_selected", "Selected Row", "Under selected songs (kept translucent)", "accent", 1),
        ("heart_empty", "Empty Heart", "The heart of songs that aren't favorites", "text", 0.45),
    ]),
    ("Artists & Albums", [
        ("artist_name", "Artist Name", "Names under artist pictures", "text", 1),
        ("album_title", "Album Title", "Names under album covers", "text", 1),
        ("card_details", "Details", "Song counts, years and artists under them", "text", 0.6),
    ]),
    ("Playlists", [
        ("playlist_name", "Playlist Name", "Names in the Playlists tab", "text", 1),
        ("playlist_details", "Playlist Details", "Song count and length under each name", "text", 0.55),
        ("playlist_rows", "Playlist Rows", "Behind the playlists list", "#ffffff", 0.08),
        ("playlist_button", "Playlist Button", "The playlists button, when the song is in one", "accent_light", 1),
        ("playlist_badge", "Playlist Count", "The badge counting the song's playlists", "accent", 1),
        ("playlist_badge_text", "Playlist Count Text", "The number on that badge", "accent_text", 1),
    ]),
    ("Now Playing", [
        ("panel_background", "Panel", "The Now Playing card (kept glassy)", "panels", 1),
        ("panel_heading", "Heading", "The “NOW PLAYING” label", "secondary_text", 0.66),
        ("panel_buttons", "Buttons", "Queue, Lyrics and Equalizer buttons", "secondary_text", 1),
        ("panel_button_active", "Active Button", "Behind the open one of those buttons", "accent", 1),
        ("panel_button_active_icon", "Active Button Icon", "The icon of the open one", "accent_text", 1),
        ("focus_button", "Focus Button", "The button that opens the focus view", "text", 1),
        ("eq_on", "Song Has EQ", "The Equalizer button when the song has its own EQ", "accent_light", 1),
        ("now_title", "Song Title", "The playing song's name", "text", 1),
        ("now_artist", "Artist", "The playing song's artist", "text", 0.9),
        ("now_album", "Album", "The playing song's album and year", "secondary_text", 1),
    ]),
    ("Player Controls", [
        ("play_button", "Play / Pause Button", "The big round button", "accent", 1),
        ("play_button_icon", "Play / Pause Icon", "Its icon", "accent_text", 1),
        ("transport", "Previous / Next", "The skip buttons", "text", 1),
        ("mode_off", "Shuffle & Repeat (Off)", "Shuffle and repeat while off", "secondary_text", 1),
        ("mode_on", "Shuffle & Repeat (On)", "Shuffle and repeat while on", "accent_light", 1),
        ("seek_bar", "Time Bar", "The played part of the time bar", "text", 1),
        ("seek_bar_hover", "Time Bar (Hovered)", "The played part while hovered or dragged", "accent", 1),
        ("seek_track", "Time Bar Track", "The rest of the time bar (kept translucent)", "text", 1),
        ("seek_knob", "Knobs", "The time and volume bar knobs", "text", 1),
        ("time_text", "Times", "Elapsed and total time", "secondary_text", 1),
        ("volume_icon", "Volume Icon", "The speaker button", "text", 1),
        ("volume_bar", "Volume Bar", "The filled part of the volume bar", "seek_bar", 1),
        ("volume_track", "Volume Track", "The rest of the volume bar (kept translucent)", "seek_track", 1),
    ]),
    ("Queue", [
        ("queue_headings", "Headings", "“Now Playing” and “Up Next”", "text", 1),
        ("now_card", "Now Playing Card", "Behind the playing song (kept translucent)", "accent", 1),
        ("now_card_title", "Now Playing Title", "The playing song's name in the queue", "accent_light", 1),
        ("now_card_artist", "Now Playing Artist", "Its artist", "text", 0.6),
        ("queue_title", "Song Title", "Song names in Up Next", "text", 1),
        ("queue_artist", "Song Artist", "Artists in Up Next", "text", 0.6),
        ("queue_duration", "Duration", "Song lengths in Up Next", "secondary_text", 1),
    ]),
    ("Lyrics & Equalizer", [
        ("lyrics_background", "Card Background", "Behind lyrics and the equalizer, under the blurred cover",
         "#141418", 1),
        ("lyrics_text", "Lyrics", "Lines that aren't being sung", "text", 1),
        ("lyrics_current", "Current Line", "The line being sung", "accent_light", 1),
        ("lyrics_messages", "Messages", "“No lyrics found”, the credit and buttons", "text", 1),
        ("eq_text", "Equalizer Text", "Titles, values and frequencies", "text", 1),
        ("eq_sliders", "Equalizer Sliders", "The filled part of each slider", "accent", 1),
        ("eq_track", "Equalizer Tracks", "The rest of each slider (kept translucent)", "text", 1),
        ("eq_reset", "Reset Button", "Behind the Reset button (kept translucent)", "text", 1),
    ]),
    ("Focus View", [
        ("focus_title", "Song Title", "The playing song's name", "text", 1),
        ("focus_artist", "Artist", "The playing song's artist", "text", 0.92),
        ("focus_album", "Album", "The playing song's album", "text", 0.62),
        ("focus_time", "Times", "Elapsed and total time", "text", 0.75),
        ("focus_buttons", "Side Buttons", "Behind the Exit, Queue, Lyrics and Equalizer buttons", "panels", 1),
        ("focus_buttons_icon", "Side Button Icons", "Their icons", "text", 1),
        ("focus_button_active", "Active Side Button", "Behind the open one", "accent", 1),
        ("focus_button_active_icon", "Active Side Button Icon", "The icon of the open one", "accent_text", 1),
        ("focus_popup", "Popup", "The Queue / Lyrics / Equalizer popup (kept glassy)", "panels", 1),
        ("focus_shade", "Shade", "The shading over the backdrop behind the song (kept translucent)",
         "#000000", 1),
    ]),
    ("Other", [
        ("resize_handle", "Resize Handle", "The line shown when resizing the Now Playing panel", "accent_light", 1),
        ("black_hole_label", "Black Hole Label", "The Black Hole mode label in its menu", "accent_light", 1),
    ]),
]
DETAILED = [key for _title, entries in GROUPS for key, *_rest in entries]
KEYS = REQUIRED + DETAILED
_FOLLOWS = {key: (follows, alpha) for _title, entries in GROUPS for key, _l, _d, follows, alpha in entries}
DEFAULT_NAME = "Default"

# Default's values match style.css and the visualizer; its accent is the system accent colour.
_DEFAULT_FIXED = {
    "accent_text": "#ffffff",
    "text": "#ffffff",
    "window": "#08080b",
    "panels": "#0d0d11",
    "favorite": "#ff4d5e",
    "wave_top": "#ff2e63",
    "wave_bottom": "#3d8bff",
}


def normalize_hex(text: str) -> str | None:
    """'#abc', 'abc', '#aabbcc' or 'aabbcc' → '#aabbcc'; anything else → None."""
    value = text.strip().lstrip("#").lower()
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6 or any(c not in "0123456789abcdef" for c in value):
        return None
    return "#" + value


def to_rgb(hex_color: str) -> tuple[float, float, float]:
    v = normalize_hex(hex_color) or "#000000"
    return tuple(int(v[i:i + 2], 16) / 255 for i in (1, 3, 5))


def to_hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def mix(a, b, t: float):
    """Blend RGB a toward RGB b by t (0..1)."""
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def css_var(key: str) -> str:
    return f"--{key.replace('_', '-')}-color"


def effective(colors: dict, key: str) -> str:
    """The hex colour `key` shows with these colours: its own, or (if unset) what it follows."""
    value = normalize_hex(colors.get(key) or "")
    if value:
        return value
    if key not in _FOLLOWS:
        return default_theme().colors.get(key, "#808080")
    follows, alpha = _FOLLOWS[key]
    if follows == "accent_light":
        base = mix(to_rgb(effective(colors, "accent")), (1, 1, 1), 0.2)
    elif follows.startswith("#"):
        base = to_rgb(follows)
    else:
        base = to_rgb(effective(colors, follows))
    if alpha < 1:
        base = mix(to_rgb(effective(colors, "panels")), base, alpha)
    return to_hex(base)


def _system_accent() -> str:
    rgba = Adw.StyleManager.get_default().get_accent_color_rgba()
    return to_hex((rgba.red, rgba.green, rgba.blue))


@dataclass
class Theme:
    name: str
    colors: dict = field(default_factory=dict)

    @property
    def is_default(self) -> bool:
        return self.name == DEFAULT_NAME


def default_theme() -> Theme:
    return Theme(DEFAULT_NAME, {"accent": _system_accent(), **_DEFAULT_FIXED})


def theme_css(colors: dict) -> str:
    """CSS that paints the app with `colors` (hex strings for every key)."""
    rgb = {k: to_rgb(colors[k]) for k in REQUIRED}
    accent = colors["accent"]
    # Accent used as a text colour on dark surfaces: a lighter shade, like libadwaita's own.
    accent_text_on_dark = to_hex(mix(rgb["accent"], (1, 1, 1), 0.2))
    text = colors["text"]
    fg_vars = "".join(f"  --{part}-fg-color: {text};\n" for part in
                      ("window", "view", "headerbar", "sidebar", "secondary-sidebar", "dialog",
                       "popover", "card", "thumbnail"))
    return (
        ":root {\n"
        f"  --accent-bg-color: {accent};\n"
        f"  --accent-color: {accent_text_on_dark};\n"
        f"  --accent-fg-color: {colors['accent_text']};\n"
        f"{fg_vars}"
        "}\n"
        "window.main-window {\n"
        f"  --text: {text};\n"
        f"  --panel-color: {colors['panels']};\n"
        f"  --window-tint: {colors['window']};\n"
        f"  --heart-color: {colors['favorite']};\n"
        + "".join(f"  {css_var(k)}: {colors[k]};\n" for k in DETAILED if colors.get(k))
        + "}\n"
    )


class ThemeManager:
    def __init__(self, db):
        self.db = db
        self.listeners: list = []  # called with no arguments after the look changes
        self._provider = Gtk.CssProvider()
        self._previewing = False
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, self._provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._custom = self._load()
        name = db.get_setting("theme", DEFAULT_NAME)
        self.active = name if name in self._custom else DEFAULT_NAME
        self._current = self.get(self.active).colors
        self._apply(self._current)

    # ---- themes ----

    def _load(self) -> dict[str, Theme]:
        themes = {}
        for entry in self.db.get_setting("themes", []) or []:
            try:
                name = str(entry["name"]).strip()
                colors = {k: normalize_hex(entry["colors"][k]) for k in REQUIRED}
                if not all(colors.values()):
                    continue
                for k in DETAILED:  # optional: keep the valid ones
                    value = normalize_hex(str(entry["colors"].get(k) or ""))
                    if value:
                        colors[k] = value
            except (KeyError, TypeError, AttributeError):
                continue
            if name and name != DEFAULT_NAME:
                themes[name] = Theme(name, colors)
        return themes

    def _store(self):
        self.db.set_setting("themes", [{"name": t.name, "colors": t.colors} for t in self._custom.values()])

    def themes(self) -> list[Theme]:
        return [default_theme(), *sorted(self._custom.values(), key=lambda t: t.name.casefold())]

    def get(self, name: str) -> Theme:
        return self._custom.get(name) or default_theme()

    def name_taken(self, name: str, except_name: str | None = None) -> bool:
        folded = name.strip().casefold()
        return any(t.name.casefold() == folded for t in self.themes() if t.name != except_name)

    def save(self, theme: Theme, old_name: str | None = None):
        """Add or update a custom theme (renaming it if old_name differs) and select it."""
        if old_name and old_name != theme.name:
            self._custom.pop(old_name, None)
        self._custom[theme.name] = Theme(theme.name, dict(theme.colors))
        self._store()
        self.select(theme.name)

    def delete(self, name: str):
        if self._custom.pop(name, None) is not None:
            self._store()
            if self.active == name:
                self.select(DEFAULT_NAME)
            else:
                self._notify()

    def select(self, name: str):
        self.active = name if name in self._custom else DEFAULT_NAME
        self.db.set_setting("theme", self.active)
        self._previewing = False
        self._current = self.get(self.active).colors
        self._apply(self._current)

    # ---- live preview while editing ----

    def preview(self, colors: dict):
        self._previewing = True
        self._current = dict(colors)
        self._apply(self._current)

    def end_preview(self):
        if self._previewing:
            self.select(self.active)

    # ---- applying ----

    @property
    def is_custom(self) -> bool:
        """Whether something other than the Default look is showing (a custom theme or a preview)."""
        return self._previewing or self.active != DEFAULT_NAME

    def colors(self) -> dict[str, tuple[float, float, float]]:
        """The colours being shown right now, as RGB floats."""
        base = default_theme().colors
        return {k: to_rgb(self._current.get(k, base[k])) for k in REQUIRED}

    def _apply(self, colors: dict):
        if not self._previewing and self.active == DEFAULT_NAME:
            css = ""  # the stylesheet's own look
        else:
            base = default_theme().colors
            css = theme_css({**colors, **{k: colors.get(k) or base[k] for k in REQUIRED}})
        self._provider.load_from_string(css)
        self._notify()

    def _notify(self):
        for listener in list(self.listeners):
            listener()
