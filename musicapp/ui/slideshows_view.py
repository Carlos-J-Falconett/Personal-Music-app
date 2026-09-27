"""Slideshows tab (Black Hole mode only): manage the background slideshows.

It holds the pages from slideshow_pages.py in a navigation view, like the Artists and Playlists
tabs: Slideshows (settings, Global and the groups) → a group → one slideshow. MainWindow shows the
tab only while Black Hole mode is on (MainWindow._sync_black_hole)."""

from gi.repository import Adw

from .slideshow_pages import SlideshowsPage, _Page


class SlideshowsView(Adw.Bin):
    """The Slideshows tab. Wraps an Adw.NavigationView (which can't be subclassed) and gives its
    pages what they need: .nav, .win, .art and .toast()."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.art = win.art
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.nav.add(SlideshowsPage(self))
        # A page that is popped stops following slideshow changes.
        self.nav.connect("popped", lambda _nav, page: page.detach() if isinstance(page, _Page) else None)

    def toast(self, text: str):
        """Show a short message at the bottom of the window."""
        self.win.toast(text)
