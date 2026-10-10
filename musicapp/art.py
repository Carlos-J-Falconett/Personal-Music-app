"""Cover art: embedded pictures (ID3/FLAC/MP4/Ogg) or folder images, scaled and cached on disk."""

import base64
import hashlib
import os
import shutil
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # noqa: E402

import mutagen  # noqa: E402

from .config import (  # noqa: E402
    ARTIST_IMAGE_DIR, ARTIST_PICTURE_DIRS, COVER_DIR, COVER_FILENAMES, IMAGE_EXTENSIONS,
)

THUMB = 128
LARGE = 512
BACKDROP = 2560  # full-window backdrop, fitted whole: its *long* side, sharp on big and HiDPI windows
BLUR = 384  # heavily blurred copy that fills the space around a fitted backdrop
# Backdrop-sized textures take tens of MB each: only the most recent few stay in memory
# (the showing one, the one fading out, the next slide); the rest reload from the disk cache.
BACKDROP_TEXTURES_KEPT = 4
# Memory kept for the other textures (covers, thumbnails, blurs, artist images), least recently used
# dropped first. Pictures on screen stay alive through their widgets anyway; a dropped one is
# reloaded from the disk cache when it's needed again.
TEXTURE_CACHE_BYTES = 64 * 1024 * 1024


def _embedded_picture(path: str) -> bytes | None:
    """The cover picture embedded in an audio file (ID3, FLAC, MP4 or Ogg), as encoded image bytes, or None.
"""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    pictures = getattr(audio, "pictures", None)  # FLAC
    if pictures:
        return pictures[0].data
    tags = audio.tags
    if tags is None:
        return None
    if hasattr(tags, "getall"):  # ID3
        frames = tags.getall("APIC")
        if frames:
            front = [f for f in frames if getattr(f, "type", 0) == 3]
            return (front or frames)[0].data
    try:
        if "covr" in tags:  # MP4
            return bytes(tags["covr"][0])
        if "metadata_block_picture" in tags:  # Ogg Vorbis / Opus
            from mutagen.flac import Picture
            return Picture(base64.b64decode(tags["metadata_block_picture"][0])).data
    except Exception:
        pass
    return None


def _folder_image(directory: str) -> str | None:
    """A cover image in the song's folder: cover/folder/front/album/albumart first, else any image; or None.
"""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    images = [n for n in names if n.lower().endswith(IMAGE_EXTENSIONS)]
    for preferred in COVER_FILENAMES:
        for n in images:
            if os.path.splitext(n)[0].lower() == preferred:
                return os.path.join(directory, n)
    return os.path.join(directory, images[0]) if images else None


SLIDE_SECONDS_DEFAULT = 10
SLIDE_SECONDS_MIN, SLIDE_SECONDS_MAX = 3, 600
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ogv")


def is_video(path) -> bool:
    """Whether a slideshow file is a video (by its type, or failing that its extension)."""
    content_type, _uncertain = Gio.content_type_guess(str(path), None)
    if Gio.content_type_is_a(content_type, "video/*"):
        return True
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTENSIONS


def _load_pixbuf(data: bytes | None = None, filename: str | None = None):
    """Decode an image from bytes or a file; None if it can't be read."""
    try:
        if filename:
            return GdkPixbuf.Pixbuf.new_from_file(filename)
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()
    except GLib.Error:
        return None


def _save_scaled(pixbuf, dest: Path, size: int):
    """Save a scaled copy: covers are shrunk so their short side is `size`; the full-window backdrop is
    fitted so its long side is `size` (enlarged too). Saved as JPEG, or PNG when the image has transparency."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    backdrop = size >= BACKDROP
    # Covers fill a square, so their short side matters; the backdrop is shown whole, so its long side does.
    scale = size / (max(w, h) if backdrop else min(w, h))
    # The backdrop is also enlarged from small sources (album covers are often ~500px): HYPER,
    # done once and cached, stays much crisper than the GPU stretching a small image full-window.
    if scale < 1 or (backdrop and scale > 1):
        interp = GdkPixbuf.InterpType.HYPER if backdrop else GdkPixbuf.InterpType.BILINEAR
        pixbuf = pixbuf.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)), interp)
    if pixbuf.get_has_alpha():
        pixbuf.savev(str(dest), "png", [], [])
    else:
        pixbuf.savev(str(dest), "jpeg", ["quality"], ["95" if backdrop else "90"])


def _save_blurred(pixbuf, dest: Path):
    """A soft, heavy blur made once on the CPU: shrink to a few pixels, then enlarge in
    smooth bilinear steps. (A GPU blur filter would be recomputed on every redraw.)"""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    for short in (24, 96, BLUR):
        scale = short / min(w, h)
        interp = GdkPixbuf.InterpType.HYPER if short == 24 else GdkPixbuf.InterpType.BILINEAR
        pixbuf = pixbuf.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)), interp)
    if pixbuf.get_has_alpha():
        pixbuf.savev(str(dest), "png", [], [])
    else:
        pixbuf.savev(str(dest), "jpeg", ["quality"], ["90"])


def _texture_bytes(texture) -> int:
    """Roughly the memory a texture takes (4 bytes a pixel)."""
    return texture.get_width() * texture.get_height() * 4


class ArtCache:
    """Every picture the app shows: album covers, artist images (normal and Black Hole mode) and the
    Black Hole slideshow's images and videos.
    Pictures are scaled once on worker threads, cached on disk (COVER_DIR, one file per key and size),
    and kept in memory as textures in two LRUs: the few full-window ones (BACKDROP_TEXTURES_KEPT) and
    everything else, up to TEXTURE_CACHE_BYTES. UI code calls the
    request_* methods and gets a texture (or None) back on the main thread.
    Black Hole mode ("turned" internally) lives here, with the slideshow settings and `slideshows`."""

    def __init__(self):
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="art")
        self._lock = threading.Lock()
        self._textures: OrderedDict[tuple[str, int], Gdk.Texture] = OrderedDict()
        self._texture_bytes = 0  # memory of the textures in self._textures (4 bytes a pixel)
        self._large_textures: OrderedDict[tuple[str, int], Gdk.Texture] = OrderedDict()
        self._missing: set[str] = set()
        self._inflight: dict[tuple[str, int], list] = {}
        # Called with the artist name after their picture changes, or None when every
        # artist's picture may have changed (Black Hole mode switched). Main thread.
        self.artist_listeners: list = []
        # Black Hole mode ("turned" internally, kept for the on-disk folder): a second set of
        # user-chosen artist images. Not remembered across launches.
        self.turned = False
        # Called (main thread) whenever the Black Hole background slideshow's images change
        # or it's switched on/off.
        self.slideshow_listeners: list = []
        self.slideshow_enabled = True
        self.slideshow_seconds = SLIDE_SECONDS_DEFAULT  # how long each image shows
        from .slideshows import Slideshows  # (it uses this module's helpers)
        self.slideshows = Slideshows(ARTIST_IMAGE_DIR / "turned" / "slideshow", self._notify_slideshow)

    @staticmethod
    def album_key(item) -> str:
        """Cache key of a track's album cover (per folder and album name)."""
        return hashlib.sha1(f"{item.dir}\0{item.album}".encode()).hexdigest()

    def artist_key(self, name: str, turned: bool | None = None) -> str:
        """Cache key of an artist image, in normal or Black Hole mode (default: the current mode)."""
        turned = self.turned if turned is None else turned
        return ("artist-turned-" if turned else "artist-") + hashlib.sha1(name.encode()).hexdigest()

    def set_turned(self, turned: bool):
        """Enter or leave Black Hole mode; artist listeners are called with None (every image may change)."""
        if turned == self.turned:
            return
        self.turned = turned
        for listener in list(self.artist_listeners):
            listener(None)

    # ---- synchronous (call from worker threads, or when a file path is needed) ----

    def cover_path(self, item, size: int = LARGE) -> Path | None:
        """Path of a cached, scaled cover for this track's album, creating it if needed."""
        key = self.album_key(item)
        return self._ensure(key, size, lambda: self._source_for_track(item))

    def _source_for_track(self, item):
        """A track's full-size cover: embedded, else a folder image; None if there's none."""
        data = _embedded_picture(item.path)
        if data:
            return _load_pixbuf(data=data)
        folder = _folder_image(item.dir)
        return _load_pixbuf(filename=folder) if folder else None

    def _ensure(self, key: str, size: int, source) -> Path | None:
        """The cached file for `key` at `size`, making it from source() if needed. None when there's no
        picture (remembered, so it isn't looked for again this session)."""
        dest = COVER_DIR / f"{key}_{size}.jpg"
        if dest.exists():
            return dest
        if key in self._missing:
            return None
        pixbuf = source()
        if pixbuf is None:
            self._missing.add(key)
            return None
        try:
            tmp = dest.with_suffix(f".{threading.get_ident()}.tmp")
            if size == BLUR:
                _save_blurred(pixbuf, tmp)
            else:
                _save_scaled(pixbuf, tmp, size)
            os.replace(tmp, dest)
        except (GLib.Error, OSError):
            return None
        return dest

    # ---- asynchronous texture loading for the UI ------------------------------

    def cached_texture(self, key: str, size: int):
        """A texture already in memory, or None."""
        return self._cached((key, size))

    def _cached(self, tex_key):
        """A texture in memory (small ones, or the recent full-window ones), or None."""
        for textures in (self._large_textures, self._textures):
            if tex_key in textures:
                textures.move_to_end(tex_key)
                return textures[tex_key]
        return None

    def _store(self, tex_key, texture):
        """Keep a texture in memory: full-window ones in their small LRU, the rest in the LRU capped at
        TEXTURE_CACHE_BYTES (the least recently used go first)."""
        if tex_key[1] >= BACKDROP:
            self._large_textures[tex_key] = texture
            self._large_textures.move_to_end(tex_key)
            while len(self._large_textures) > BACKDROP_TEXTURES_KEPT:
                self._large_textures.popitem(last=False)
            return
        self._forget_texture(tex_key)
        self._textures[tex_key] = texture
        self._texture_bytes += _texture_bytes(texture)
        while self._texture_bytes > TEXTURE_CACHE_BYTES and len(self._textures) > 1:
            self._forget_texture(next(iter(self._textures)))

    def _forget_texture(self, tex_key):
        """Drop a texture from the memory cache (if it's there)."""
        texture = self._textures.pop(tex_key, None)
        if texture is not None:
            self._texture_bytes -= _texture_bytes(texture)

    def request_track(self, item, size: int, callback):
        """callback(texture_or_None) is invoked on the main thread."""
        key = self.album_key(item)
        self._request(key, size, callback, lambda: self._source_for_track(item))

    def request_artist(self, name: str, fallback_item, size: int, callback):
        """callback(texture_or_None) on the main thread with an artist's image: the user's own image
        (in Black Hole mode, the Black Hole one first), else a MusicBee artist picture, else the cover of
        `fallback_item`."""
        # Black Hole mode falls back to the normal image for artists without a Black Hole one.
        modes = (True, False) if self.turned else (False,)

        def source():
            """The artist's full-size picture, by the priority above."""
            for turned in modes:
                custom = self.custom_artist_image(name, turned)
                if custom:
                    pixbuf = _load_pixbuf(filename=str(custom))
                    if pixbuf:
                        return pixbuf
            for d in ARTIST_PICTURE_DIRS:
                for ext in IMAGE_EXTENSIONS:
                    candidate = d / f"{name}{ext}"
                    if candidate.exists():
                        pixbuf = _load_pixbuf(filename=str(candidate))
                        if pixbuf:
                            return pixbuf
            return self._source_for_track(fallback_item) if fallback_item else None

        self._request(self.artist_key(name), size, callback, source)

    # ---- user-chosen artist images ----

    @staticmethod
    def _custom_stem(name: str) -> str:
        """File name (without extension) of an artist's user-chosen image."""
        return hashlib.sha1(name.encode()).hexdigest()

    def _custom_dir(self, turned: bool | None = None) -> Path:
        """Folder of the user's artist images for normal or Black Hole mode."""
        turned = self.turned if turned is None else turned
        return ARTIST_IMAGE_DIR / "turned" if turned else ARTIST_IMAGE_DIR

    def custom_artist_image(self, name: str, turned: bool | None = None) -> Path | None:
        """The user's image for `name` in the given mode (default: the current mode), no fallback."""
        stem = self._custom_stem(name)
        folder = self._custom_dir(turned)
        for ext in IMAGE_EXTENSIONS:
            candidate = folder / f"{stem}{ext}"
            if candidate.exists():
                return candidate
        return None

    def set_artist_image(self, name: str, src: str):
        """Use `src` as the artist's picture. Raises ValueError if it isn't a readable image."""
        if _load_pixbuf(filename=src) is None:
            raise ValueError("That file isn't an image this app can read.")
        ext = os.path.splitext(src)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            ext = ".png"
        folder = self._custom_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self._remove_custom(name)
        shutil.copyfile(src, folder / f"{self._custom_stem(name)}{ext}")
        self._invalidate_artist(name)

    def reset_artist_image(self, name: str):
        """Remove the user's image for the artist (in the current mode)."""
        self._remove_custom(name)
        self._invalidate_artist(name)

    def _remove_custom(self, name: str):
        """Delete every file of the artist's user image in the current mode."""
        existing = self.custom_artist_image(name)
        while existing is not None:
            existing.unlink(missing_ok=True)
            existing = self.custom_artist_image(name)

    def _invalidate_artist(self, name: str):
        """Forget cached copies of an artist's image and tell the artist listeners."""
        # A normal-mode change also affects Black Hole mode, which falls back to the normal image.
        keys = [self.artist_key(name)] if self.turned else [self.artist_key(name, False), self.artist_key(name, True)]
        for key in keys:
            for cached in COVER_DIR.glob(f"{key}_*.jpg"):
                cached.unlink(missing_ok=True)
            for tex_key in [k for k in self._textures if k[0] == key]:
                self._forget_texture(tex_key)
            for tex_key in [k for k in self._large_textures if k[0] == key]:
                del self._large_textures[tex_key]
            self._missing.discard(key)
        for listener in list(self.artist_listeners):
            listener(name)

    # ---- Black Hole background slideshows (see slideshows.py) ----

    def slideshow_images(self) -> list[Path]:
        """The images and videos of the slideshow that's selected (an individual one, a group's,
        or the Global one), in order."""
        return self.slideshows.active_media()

    def request_slide(self, path: Path, size: int, callback):
        """callback(texture_or_None) on the main thread; scaled copies are cached like covers."""
        try:
            stamp = Path(path).stat().st_mtime_ns
        except OSError:
            callback(None)
            return
        key = "slide-" + hashlib.sha1(f"{path}\0{stamp}".encode()).hexdigest()
        self._request(key, size, callback, lambda: _load_pixbuf(filename=str(path)))

    def set_slideshow_seconds(self, seconds: int):
        """How long each slideshow image shows (clamped to SLIDE_SECONDS_MIN..MAX)."""
        seconds = int(min(max(seconds, SLIDE_SECONDS_MIN), SLIDE_SECONDS_MAX))
        if seconds != self.slideshow_seconds:
            self.slideshow_seconds = seconds
            self._notify_slideshow()

    def set_slideshow_enabled(self, enabled: bool):
        """Turn the Black Hole slideshow on or off."""
        if enabled != self.slideshow_enabled:
            self.slideshow_enabled = enabled
            self._notify_slideshow()

    def _notify_slideshow(self):
        """Call the slideshow listeners (something about the slideshow changed)."""
        for listener in list(self.slideshow_listeners):
            listener()

    def _request(self, key, size, callback, source):
        """Deliver a texture from memory, or load it on a worker thread; callers asking for the same
        picture meanwhile share the one load."""
        tex = self._cached((key, size))
        if tex is not None or key in self._missing:
            callback(tex)
            return
        with self._lock:
            waiting = self._inflight.get((key, size))
            if waiting is not None:
                waiting.append(callback)
                return
            self._inflight[(key, size)] = [callback]
        self._pool.submit(self._load, key, size, source)

    def _load(self, key, size, source):
        """Worker thread: make sure the scaled file exists and load it as a texture."""
        texture = None
        try:
            path = self._ensure(key, size, source)
            if path:
                texture = Gdk.Texture.new_from_filename(str(path))
        except Exception as exc:
            print(f"[art] {exc}")
        GLib.idle_add(self._deliver, key, size, texture)

    def _deliver(self, key, size, texture):
        """Main thread: keep the texture and call everyone who asked for it."""
        if texture is not None:
            self._store((key, size), texture)
        with self._lock:
            callbacks = self._inflight.pop((key, size), [])
        for cb in callbacks:
            cb(texture)
        return False

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
