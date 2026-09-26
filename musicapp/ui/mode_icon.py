"""Images that show the Sun (normal mode) or the Black Hole (Black Hole mode) and flip
between them when the mode switches. Used for the top-left menu icon and the About icon."""

from gi.repository import Gdk, GLib, Gtk

from ..config import BLACK_HOLE_ICON, SUN_ICON

FLIP_HALF_MS = 180  # matches the .mode-flip-out / .mode-flip-in CSS animations

_textures: dict[bool, Gdk.Texture] = {}


def mode_texture(black_hole: bool) -> Gdk.Texture:
    if black_hole not in _textures:
        _textures[black_hole] = Gdk.Texture.new_from_filename(str(BLACK_HOLE_ICON if black_hole else SUN_ICON))
    return _textures[black_hole]


def follow_mode(image: Gtk.Image, art):
    """Show the current mode's image in `image` and flip it whenever the mode switches.
    (Black Hole mode is `art.turned` internally.)"""
    image.set_from_paintable(mode_texture(art.turned))

    def on_change(name):
        if name is None:  # None: the mode switched
            flip_to(image, mode_texture(art.turned))

    # Listen only while on screen: menus build a new icon each time they open, and a listener
    # kept for good would keep every old icon alive.
    def on_map(*_):
        image.set_from_paintable(mode_texture(art.turned))
        if on_change not in art.artist_listeners:
            art.artist_listeners.append(on_change)

    def on_unmap(*_):
        if on_change in art.artist_listeners:
            art.artist_listeners.remove(on_change)

    image.connect("map", on_map)
    image.connect("unmap", on_unmap)
    if image.get_mapped():
        on_map()


def flip_to(image: Gtk.Image, paintable):
    """Turn the image edge-on, swap the picture, and turn the new one into view."""
    if not image.get_mapped():
        image.set_from_paintable(paintable)
        return
    image.remove_css_class("mode-flip-in")
    image.add_css_class("mode-flip-out")

    def swap():
        image.set_from_paintable(paintable)
        image.remove_css_class("mode-flip-out")
        image.add_css_class("mode-flip-in")
        GLib.timeout_add(FLIP_HALF_MS + 50, lambda: image.remove_css_class("mode-flip-in") or False)
        return False

    GLib.timeout_add(FLIP_HALF_MS, swap)


class ModeIcon(Gtk.Image):
    def __init__(self, art, pixel_size: int = 22):
        super().__init__(pixel_size=pixel_size)
        self.add_css_class("mode-icon")
        follow_mode(self, art)
