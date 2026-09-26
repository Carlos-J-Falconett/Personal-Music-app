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


def _embedded_picture(path: str) -> bytes | None:
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


class ArtCache:
    def __init__(self):
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="art")
        self._lock = threading.Lock()
        self._textures: dict[tuple[str, int], Gdk.Texture] = {}
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
        self.slideshow_seconds = SLIDE_SECONDS_DEFAULT  # how long each slide shows (videos: at most)

    @staticmethod
    def album_key(item) -> str:
        return hashlib.sha1(f"{item.dir}\0{item.album}".encode()).hexdigest()

    def artist_key(self, name: str, turned: bool | None = None) -> str:
        turned = self.turned if turned is None else turned
        return ("artist-turned-" if turned else "artist-") + hashlib.sha1(name.encode()).hexdigest()

    def set_turned(self, turned: bool):
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
        data = _embedded_picture(item.path)
        if data:
            return _load_pixbuf(data=data)
        folder = _folder_image(item.dir)
        return _load_pixbuf(filename=folder) if folder else None

    def _ensure(self, key: str, size: int, source) -> Path | None:
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
        return self._cached((key, size))

    def _cached(self, tex_key):
        if tex_key in self._large_textures:
            self._large_textures.move_to_end(tex_key)
            return self._large_textures[tex_key]
        return self._textures.get(tex_key)

    def _store(self, tex_key, texture):
        if tex_key[1] >= BACKDROP:
            self._large_textures[tex_key] = texture
            self._large_textures.move_to_end(tex_key)
            while len(self._large_textures) > BACKDROP_TEXTURES_KEPT:
                self._large_textures.popitem(last=False)
        else:
            self._textures[tex_key] = texture

    def request_track(self, item, size: int, callback):
        """callback(texture_or_None) is invoked on the main thread."""
        key = self.album_key(item)
        self._request(key, size, callback, lambda: self._source_for_track(item))

    def request_artist(self, name: str, fallback_item, size: int, callback):
        # Black Hole mode falls back to the normal image for artists without a Black Hole one.
        modes = (True, False) if self.turned else (False,)

        def source():
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
        return hashlib.sha1(name.encode()).hexdigest()

    def _custom_dir(self, turned: bool | None = None) -> Path:
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
        self._remove_custom(name)
        self._invalidate_artist(name)

    def _remove_custom(self, name: str):
        existing = self.custom_artist_image(name)
        while existing is not None:
            existing.unlink(missing_ok=True)
            existing = self.custom_artist_image(name)

    def _invalidate_artist(self, name: str):
        # A normal-mode change also affects Black Hole mode, which falls back to the normal image.
        keys = [self.artist_key(name)] if self.turned else [self.artist_key(name, False), self.artist_key(name, True)]
        for key in keys:
            for cached in COVER_DIR.glob(f"{key}_*.jpg"):
                cached.unlink(missing_ok=True)
            for textures in (self._textures, self._large_textures):
                for tex_key in [k for k in textures if k[0] == key]:
                    del textures[tex_key]
            self._missing.discard(key)
        for listener in list(self.artist_listeners):
            listener(name)

    # ---- Black Hole background slideshow (one global set of images) ----

    @property
    def slideshow_dir(self) -> Path:
        return ARTIST_IMAGE_DIR / "turned" / "slideshow"

    def slideshow_images(self) -> list[Path]:
        """The slideshow's images and videos in the order they were added."""
        try:
            return sorted(p for p in self.slideshow_dir.iterdir()
                          if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)
        except OSError:
            return []

    def add_slideshow_images(self, paths, done=None):
        """Copy readable images and videos into the slideshow, after the existing ones. Copying runs
        in the background (videos can be large); done(added, skipped) is called on the main thread."""
        folder = self.slideshow_dir
        existing = self.slideshow_images()
        start = max((int(p.name[:4]) for p in existing if p.name[:4].isdigit()), default=0)

        def work():
            folder.mkdir(parents=True, exist_ok=True)
            number, added, skipped = start, 0, 0
            for src in paths:
                name = os.path.basename(src)
                ext = os.path.splitext(name)[1].lower()
                if is_video(src):
                    if ext not in VIDEO_EXTENSIONS:
                        skipped += 1
                        continue
                elif _load_pixbuf(filename=str(src)) is None:
                    skipped += 1
                    continue
                elif ext not in IMAGE_EXTENSIONS:
                    name += ".png"
                number += 1
                try:
                    shutil.copyfile(src, folder / f"{number:04d}-{name}")
                    added += 1
                except OSError:
                    skipped += 1

            def finish():
                if added:
                    self._notify_slideshow()
                if done is not None:
                    done(added, skipped)
                return False
            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def remove_slideshow_image(self, path: Path):
        Path(path).unlink(missing_ok=True)
        self._notify_slideshow()

    def clear_slideshow(self):
        for path in self.slideshow_images():
            path.unlink(missing_ok=True)
        self._notify_slideshow()

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
        seconds = int(min(max(seconds, SLIDE_SECONDS_MIN), SLIDE_SECONDS_MAX))
        if seconds != self.slideshow_seconds:
            self.slideshow_seconds = seconds
            self._notify_slideshow()

    def set_slideshow_enabled(self, enabled: bool):
        if enabled != self.slideshow_enabled:
            self.slideshow_enabled = enabled
            self._notify_slideshow()

    def _notify_slideshow(self):
        for listener in list(self.slideshow_listeners):
            listener()

    def _request(self, key, size, callback, source):
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
        texture = None
        try:
            path = self._ensure(key, size, source)
            if path:
                texture = Gdk.Texture.new_from_filename(str(path))
        except Exception as exc:
            print(f"[art] {exc}")
        GLib.idle_add(self._deliver, key, size, texture)

    def _deliver(self, key, size, texture):
        if texture is not None:
            self._store((key, size), texture)
        with self._lock:
            callbacks = self._inflight.pop((key, size), [])
        for cb in callbacks:
            cb(texture)
        return False

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
