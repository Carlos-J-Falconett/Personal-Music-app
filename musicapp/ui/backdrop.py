"""Full-window background showing the playing song's artist image, crossfading between artists.
The image is fitted whole (never cropped); a blurred, darkened copy of it fills the rest of the window.
In Black Hole mode, a slideshow of the user's images and videos (if any) replaces the artist image.
Each image shows for the chosen number of seconds; a video plays (without sound) to its end."""

import time

from gi.repository import GLib, Gtk

from ..art import BACKDROP, BLUR, is_video
from .silent_video import SilentVideo

FADE_MS = 800


class Backdrop(Gtk.Stack):
    """The window's background (see the module docstring). Shows the playing artist's image, or in
    Black Hole mode, while a slideshow is on and has images or videos, the slideshow instead.
    Two layers take turns so each change crossfades. The slideshow keeps its own position, timer
    (pausable, for Repeat Current Slide) and video player; ArtCache's slideshow listeners tell it
    when the slides, their settings or the selected slideshow change."""

    def __init__(self, win):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=FADE_MS,
                         hhomogeneous=False, vhomogeneous=False)
        self.add_css_class("backdrop")
        self.win = win
        self.art = win.art
        self.set_can_target(False)  # purely decorative
        self._artist: str | None = None
        self._want = None  # what the next delivered texture must be for: an artist name or ("slide", path)
        # Slideshow state.
        self._slides: list = []
        self._slide_index = 0
        self._slide_timer = 0  # ends the showing slide (one-shot)
        self._slideshow_on = False
        self._media: SilentVideo | None = None  # the video showing, if any
        self._loading_media: SilentVideo | None = None  # a video getting ready to show
        self._failures = 0  # slides in a row that couldn't be shown
        # Repeat: the showing slide stays (an image's timer is paused, a video loops).
        self._repeat = False
        self._timer_due = 0.0  # when the running slide timer fires (monotonic seconds)
        self._timer_left: float | None = None  # a paused timer's remaining seconds
        # Two layers that take turns, so the old image fades out as the new one fades in.
        # Each is: blurred copy filling the window → dark veil → the whole image, fitted.
        self._layers = []
        for name in ("a", "b"):
            layer = Gtk.Overlay()
            fill = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            fill.add_css_class("backdrop-fill")
            layer.set_child(fill)
            veil = Gtk.Box()
            veil.add_css_class("backdrop-veil")
            layer.add_overlay(veil)
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
            picture.add_css_class("backdrop-image")
            layer.add_overlay(picture)
            self._layers.append((fill, picture))
            self.add_named(layer, name)
        self.add_named(Gtk.Box(), "empty")
        self.set_visible_child_name("empty")

        win.player.connect("track-changed", lambda _p, track: self._show_track(track))
        self.art.artist_listeners.append(self._on_artist_image_changed)
        self.art.slideshow_listeners.append(self._on_slides_changed)
        self._slides = self.art.slideshow_images()
        self._selection = self.art.slideshows.selection_serial
        self._seconds = self.art.slideshow_seconds
        # Videos pause while the window is hidden, like the slideshow itself.
        self.connect("map", lambda *_: self._media.play() if self._media is not None else None)
        self.connect("unmap", lambda *_: self._media.pause() if self._media is not None else None)
        self._show_track(win.player.current)

    # ---- artist image ----

    def _show_track(self, track, force=False):
        """A new song: show its artist's image (unless it's the same artist), or keep the slideshow going."""
        if self._slideshow_active():
            self._start_slideshow()  # keeps running across songs
            return
        artist = track.artist_key if track is not None else None
        if artist == self._artist and not force:
            return  # same artist: keep the current image
        self._artist = artist
        self._want = artist
        if track is None:
            self.set_visible_child_name("empty")
            return
        self._load(artist, lambda size, cb: self.art.request_artist(artist, track, size, cb))

    def _load(self, token, request):
        """request(size, callback) fetches one size of the image; show it once the sharp
        image and its blurred fill are both ready."""
        def got_sharp(texture):
            """The sharp image arrived: now fetch its blurred fill."""
            if token != self._want:
                return  # something newer was requested meanwhile
            if texture is None:
                self._apply(token, None, None)
                return
            request(BLUR, lambda blurred: self._apply(token, texture, blurred))
        request(BACKDROP, got_sharp)

    def _apply(self, token, texture, blurred):
        """Both images are ready: crossfade to them (or, with no image, to the empty background)."""
        if token != self._want:
            return
        if texture is None:
            if not isinstance(token, tuple):
                self.set_visible_child_name("empty")
            return
        self._show(texture, blurred)

    def _show(self, paintable, blurred=None, media=None):
        """Crossfade to a new picture in the hidden layer. A video (media) fills the window with
        a blurred copy of itself; an image uses its pre-blurred texture."""
        self._failures = 0
        showing = self.get_visible_child_name()
        name = "b" if showing == "a" else "a"
        fill, picture = self._layers[0 if name == "a" else 1]
        fill.set_paintable(media.paintable if media is not None else blurred)
        (fill.add_css_class if media is not None else fill.remove_css_class)("backdrop-video-fill")
        picture.set_paintable(paintable)
        self.set_visible_child_name(name)
        old, self._media = self._media, media
        if old is not None and old is not media:
            old.pause()
            # Let it fade out on its last frame, then let it go.
            GLib.timeout_add(FADE_MS + 100, lambda: self._release(old))

    def _release(self, media):
        """A video that has faded out: take it off the pictures and stop its player."""
        for fill, picture in self._layers:
            for pic in (fill, picture):
                if pic.get_paintable() is media.paintable:
                    pic.set_paintable(None)
        media.stop()
        return False

    @property
    def artist(self):
        """The artist whose image is showing (None during the slideshow)."""
        return self._artist

    def current_texture(self):
        """The picture showing, if any."""
        name = self.get_visible_child_name()
        if name not in ("a", "b"):
            return None
        return self._layers[0 if name == "a" else 1][1].get_paintable()

    def _on_artist_image_changed(self, name):
        """An artist image changed, or Black Hole mode switched (name None)."""
        if name is None:  # Black Hole mode switched: slideshow on/off, or other artist images
            self._update_mode(reload_artist=True)
        elif name == self._artist and not self._slideshow_active():
            self._show_track(self.win.player.current, force=True)

    # ---- Black Hole slideshow ----

    def _slideshow_active(self) -> bool:
        """Whether the slideshow should be showing: Black Hole mode, slideshow on, slides to show,
        and a song loaded."""
        return (self.art.turned and self.art.slideshow_enabled and bool(self._slides)
                and self.win.player.current is not None)

    def _update_mode(self, reload_artist=False):
        """Start or stop the slideshow to match _slideshow_active()."""
        if self._slideshow_active():
            self._start_slideshow()
        else:
            self._stop_slideshow(reload_artist)

    def _start_slideshow(self):
        """Start showing slides, from the current position."""
        if self._slideshow_on:
            return
        self._slideshow_on = True
        self._artist = None  # the artist image must be reloaded when the slideshow ends
        self._slide_index %= len(self._slides)
        self._show_slide()

    def _stop_slideshow(self, reload_artist=False):
        """Stop the slideshow (and repeat), and show the artist image again."""
        was_running = self._slideshow_on
        self._slideshow_on = False
        self._repeat = False
        self._cancel_timer()
        self._set_loading(None)
        if self._media is not None:
            self._media.pause()
        if was_running or reload_artist:
            self._show_track(self.win.player.current, force=True)

    def _cancel_timer(self):
        """Stop the slide timer (forgetting any paused remainder)."""
        if self._slide_timer:
            GLib.source_remove(self._slide_timer)
            self._slide_timer = 0
        self._timer_left = None

    def _schedule(self, seconds: float | None = None):
        """The showing slide ends after `seconds` (default: the chosen slide time). While
        repeating, the time is kept for later instead: the slide stays."""
        self._cancel_timer()
        seconds = self._seconds if seconds is None else seconds
        if self._repeat:
            self._timer_left = seconds
            return
        self._timer_due = time.monotonic() + seconds
        self._slide_timer = GLib.timeout_add(max(1, int(seconds * 1000)), self._on_timer)

    def _on_timer(self):
        """The slide's time is up: next slide."""
        self._slide_timer = 0
        self._next_slide()
        return False

    # ---- controls (Black Hole mode shortcuts) ----

    @property
    def slideshow_playing(self) -> bool:
        """Whether slides are showing (the arrow keys work only then)."""
        return self._slideshow_on and bool(self._slides)

    @property
    def repeating(self) -> bool:
        """Whether Repeat Current Slide is on."""
        return self._repeat

    def set_repeat(self, on: bool):
        """Keep showing the current slide (an image's time is paused, a video loops), or carry
        on: an image with the time it had left, a video to its end. The order doesn't change."""
        if on == self._repeat:
            return
        if on:
            left = max(0.0, self._timer_due - time.monotonic()) if self._slide_timer else self._timer_left
            self._cancel_timer()
            self._timer_left = left
            self._repeat = True
        else:
            self._repeat = False
            left, self._timer_left = self._timer_left, None
            if left is not None and self._slideshow_on:
                self._schedule(left)
        for media in (self._media, self._loading_media):
            if media is not None:
                media.set_loop(self._repeat or len(self._slides) == 1)

    def step_slide(self, step: int) -> bool:
        """Show the next (+1) or previous (-1) slide now. False if no slideshow is playing."""
        if not (self._slideshow_on and self._slides):
            return False
        self._slide_index = (self._slide_index + step) % len(self._slides)
        self._failures = 0
        self._show_slide()
        return True

    def _show_slide(self):
        """Show the slide at the current position (loading a video, or an image and its blurred fill),
        start its timer, and warm up the next image."""
        path = self._slides[self._slide_index]
        token = ("slide", path)
        self._want = token
        # Ends an image after the chosen time. For a video it only matters if the video never
        # gets going (it's cancelled once it shows): a showing video moves on when it ends.
        self._schedule()
        if is_video(path):
            self._load_video(token, path)
        else:
            self._set_loading(None)
            self._load(token, lambda size, cb: self.art.request_slide(path, size, cb))
        if len(self._slides) > 1:
            # Warm up the next image now, so it's ready when its turn comes.
            upcoming = self._slides[(self._slide_index + 1) % len(self._slides)]
            if not is_video(upcoming):
                self.art.request_slide(upcoming, BACKDROP, lambda _texture: None)
                self.art.request_slide(upcoming, BLUR, lambda _texture: None)

    def _load_video(self, token, path):
        """Load a slideshow video (without sound); it shows once its first frame is ready."""
        media = SilentVideo(path)  # no sound: see silent_video.py
        media.set_loop(self._repeat or len(self._slides) == 1)  # alone or repeated, it plays on and on
        self._set_loading(media)

        def ready(*_):
            """The video is ready (show and play it) or failed (skip it, see below)."""
            if token != self._want or media is not self._loading_media:
                return  # something newer was asked for meanwhile
            if media.get_error() is not None:
                self._set_loading(None)
                # Unplayable: skip it, but not from inside this call (the next one may fail
                # at once too), and once every slide has failed, wait for the next slide time.
                self._failures += 1
                if self._failures < len(self._slides):
                    GLib.idle_add(lambda: token == self._want and self._slideshow_on
                                  and self._next_slide() and False)
                return
            if not media.is_prepared():
                return
            self._loading_media = None
            self._cancel_timer()  # videos play to their end, however long the slide time
            self._timer_left = None
            self._show(media.paintable, media=media)
            if self.get_mapped():
                media.play()

        def ended(*_):
            """The video is over: on to the next slide."""
            # The video is over: on to the next slide.
            if media.get_ended() and media is self._media and self._slideshow_on and len(self._slides) > 1:
                self._next_slide()

        media.connect("notify::prepared", ready)
        media.connect("notify::error", ready)
        media.connect("notify::ended", ended)
        ready()

    def _set_loading(self, media):
        """A video that was still getting ready and is no longer wanted is let go."""
        old, self._loading_media = self._loading_media, media
        if old is not None and old is not media and old is not self._media:
            old.stop()

    def _next_slide(self):
        """Move on to the next slide (or stop, if the slideshow shouldn't show any more)."""
        if not self._slideshow_active():  # e.g. the queue was cleared
            self._stop_slideshow(reload_artist=True)
            return
        if not self.get_mapped():  # don't churn while the window is hidden; check again later
            self._schedule()
            return
        if len(self._slides) > 1:
            self._slide_index = (self._slide_index + 1) % len(self._slides)
            self._show_slide()
        else:
            self._schedule()

    def _on_slides_changed(self):
        """Slides, settings or the selected slideshow changed: restart on a new selection, or stay on
        the showing slide if it's still there."""
        showing = self._slides[self._slide_index] if self._slides else None
        self._slides = self.art.slideshow_images()
        self._seconds = self.art.slideshow_seconds  # takes effect from the next slide
        self._failures = 0
        if self._selection != self.art.slideshows.selection_serial:
            # Another slideshow was picked: start it from its first slide, right away.
            self._selection = self.art.slideshows.selection_serial
            self._slide_index = 0
            if self._slideshow_on and self._slides:
                self._show_slide()
        elif showing in self._slides:
            self._slide_index = self._slides.index(showing)
            if self._media is not None:
                self._media.set_loop(self._repeat or len(self._slides) == 1)
        elif self._slides:
            # The showing slide was removed: the one after it takes its place now.
            self._slide_index %= len(self._slides)
            if self._slideshow_on:
                self._show_slide()
        self._update_mode()
