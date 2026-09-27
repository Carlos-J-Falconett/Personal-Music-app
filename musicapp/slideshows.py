"""Black Hole mode's background slideshows, in three levels:

- an individual slideshow has its own images and videos (copied into its own folder);
- a group holds individual slideshows, and shows all of their images and videos as one;
- the Global slideshow shows everything, from every group.

One of these is showing at a time (the selection, remembered across launches). The structure and
the selection live in slideshows.json next to the slideshows' folders."""

import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib

from .art import VIDEO_EXTENSIONS, _load_pixbuf, is_video
from .config import IMAGE_EXTENSIONS

STATE_FILE = "slideshows.json"
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS
GLOBAL, GROUP, SLIDESHOW = "global", "group", "slideshow"


@dataclass(eq=False)  # compared by identity: slideshows and groups point at each other
class Slideshow:
    """An individual slideshow; its files live in its own folder (Slideshows.folder)."""
    id: str
    name: str
    group: "Group" = field(repr=False)


@dataclass(eq=False)
class Group:
    """A group of individual slideshows; showing it shows all of theirs, in order."""
    id: str
    name: str
    slideshows: list[Slideshow] = field(default_factory=list)


def _new_id() -> str:
    """A new id for a group or slideshow (also its folder name)."""
    return uuid.uuid4().hex[:12]


def _media_in(folder: Path) -> list[Path]:
    """A folder's images and videos, in the order they were added (their number prefix)."""
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS)
    except OSError:
        return []


class Slideshows:
    """The groups, their slideshows and their files, and which one is showing (see the module docstring).
    Everything is saved to slideshows.json after each change; notify() tells ArtCache's slideshow
    listeners (the backdrop, open dialogs) that something changed."""

    def __init__(self, root: Path, notify):
        """notify() is called on the main thread after anything changes."""
        self.root = root
        self._notify = notify
        self.groups: list[Group] = []
        # What's showing: kind, plus the group and individual last used (kept while showing
        # Global, so the shortcuts carry on from there).
        self._kind = GLOBAL
        self._group_id: str | None = None
        self._slideshow_id: str | None = None
        self.selection_serial = 0  # goes up whenever the selection changes
        self._load()

    # ---- saving and loading ----

    def _load(self):
        """Read slideshows.json; migrate the old single-slideshow folder or rebuild from the folders if
        it's missing or damaged."""
        path = self.root / STATE_FILE
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            self._migrate()
            return
        except (OSError, ValueError) as exc:
            print(f"[slideshows] can't read {path}: {exc}; rebuilding from the folders")
            self._recover()
            return
        try:
            for g in data.get("groups", []):
                group = Group(str(g["id"]), str(g["name"]))
                group.slideshows = [Slideshow(str(s["id"]), str(s["name"]), group) for s in g.get("slideshows", [])]
                self.groups.append(group)
            active = data.get("active") or {}
            self._kind = active.get("kind", GLOBAL)
            self._group_id = active.get("group")
            self._slideshow_id = active.get("slideshow")
        except (KeyError, TypeError, AttributeError) as exc:
            print(f"[slideshows] {path} is damaged ({exc}); rebuilding from the folders")
            self.groups = []
            self._recover()
            return
        self._check_selection()

    def _migrate(self):
        """Before groups existed, the images and videos sat directly in the folder: they become
        the first slideshow of a first group."""
        loose = _media_in(self.root)
        if not loose:
            return
        group = self._add_group("Group 1")
        slideshow = self._add_slideshow(group, "Slideshow 1")
        folder = self.folder(slideshow)
        folder.mkdir(parents=True, exist_ok=True)
        for path in loose:
            os.replace(path, folder / path.name)
        self._save()

    def _recover(self):
        """The structure file is unreadable: keep every slideshow folder, in one group."""
        folders = sorted(p for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else []
        if folders:
            group = Group(_new_id(), "Recovered")
            group.slideshows = [Slideshow(p.name, f"Slideshow {i}", group) for i, p in enumerate(folders, 1)]
            self.groups.append(group)
        self._kind, self._group_id, self._slideshow_id = GLOBAL, None, None
        self._save()

    def _save(self):
        """Write slideshows.json (atomically)."""
        data = {
            "groups": [{"id": g.id, "name": g.name,
                        "slideshows": [{"id": s.id, "name": s.name} for s in g.slideshows]}
                       for g in self.groups],
            "active": {"kind": self._kind, "group": self._group_id, "slideshow": self._slideshow_id},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f"{STATE_FILE}.tmp"
        try:
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, self.root / STATE_FILE)
        except OSError as exc:
            print(f"[slideshows] can't save: {exc}")

    def _changed(self, selection=False):
        """Save and notify; `selection` marks a change of what's showing."""
        if selection:
            self.selection_serial += 1
        self._save()
        self._notify()

    # ---- looking things up ----

    def folder(self, slideshow: Slideshow) -> Path:
        """The folder holding a slideshow's images and videos."""
        return self.root / slideshow.id

    def find_group(self, group_id) -> Group | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def find_slideshow(self, slideshow_id) -> Slideshow | None:
        return next((s for g in self.groups for s in g.slideshows if s.id == slideshow_id), None)

    def media(self, slideshow: Slideshow) -> list[Path]:
        """A slideshow's images and videos, in the order they were added."""
        return _media_in(self.folder(slideshow))

    def group_media(self, group: Group) -> list[Path]:
        """All of a group's images and videos, slideshow by slideshow."""
        return [p for s in group.slideshows for p in self.media(s)]

    def all_media(self) -> list[Path]:
        """Everything, group by group (the Global slideshow)."""
        return [p for g in self.groups for p in self.group_media(g)]

    # ---- what's showing ----

    @property
    def kind(self) -> str:
        """What's showing: GLOBAL, GROUP or SLIDESHOW."""
        return self._kind

    @property
    def active_group(self) -> Group | None:
        """The group showing, or holding the slideshow showing (while Global: the last one used)."""
        return self.find_group(self._group_id)

    @property
    def active_slideshow(self) -> Slideshow | None:
        """The individual slideshow showing, if one is."""
        return self.find_slideshow(self._slideshow_id) if self._kind == SLIDESHOW else None

    def active_media(self) -> list[Path]:
        """The images and videos of whatever is showing."""
        if self._kind == SLIDESHOW and (s := self.find_slideshow(self._slideshow_id)):
            return self.media(s)
        if self._kind == GROUP and (g := self.find_group(self._group_id)):
            return self.group_media(g)
        return self.all_media()

    def is_showing(self, item) -> bool:
        """Whether `item` (None for Global, a Group or a Slideshow) is what's selected."""
        if item is None:
            return self._kind == GLOBAL
        if isinstance(item, Group):
            return self._kind == GROUP and self._group_id == item.id
        return self._kind == SLIDESHOW and self._slideshow_id == item.id

    def describe_active(self) -> str:
        """What's showing, for messages: "Group › Slideshow", "Group: …" or "Global slideshow"."""
        if self._kind == SLIDESHOW and (s := self.find_slideshow(self._slideshow_id)):
            return f"{s.group.name} › {s.name}"
        if self._kind == GROUP and (g := self.find_group(self._group_id)):
            return f"Group: {g.name}"
        return "Global slideshow"

    def _check_selection(self):
        """Forget a selection whose group or slideshow is gone: Global shows instead."""
        slideshow = self.find_slideshow(self._slideshow_id)
        if slideshow is None:
            self._slideshow_id = None
        elif self._group_id != slideshow.group.id:
            self._group_id = slideshow.group.id
        if self.find_group(self._group_id) is None:
            self._group_id = None
        if (self._kind == SLIDESHOW and self._slideshow_id is None) or \
                (self._kind == GROUP and self._group_id is None) or self._kind not in (GLOBAL, GROUP, SLIDESHOW):
            self._kind = GLOBAL

    # ---- choosing what shows ----

    def select_global(self):
        """Show the Global slideshow."""
        self._kind = GLOBAL
        self._changed(selection=True)

    def select_group(self, group: Group):
        """Show a whole group."""
        if self._group_id != group.id:
            self._slideshow_id = None
        self._kind, self._group_id = GROUP, group.id
        self._changed(selection=True)

    def select_slideshow(self, slideshow: Slideshow):
        """Show one individual slideshow."""
        self._kind, self._group_id, self._slideshow_id = SLIDESHOW, slideshow.group.id, slideshow.id
        self._changed(selection=True)

    def _current_group(self) -> Group | None:
        """The group the shortcuts work in: the active one, else the first with slideshows."""
        group = self.find_group(self._group_id)
        if group is not None and group.slideshows:
            return group
        return next((g for g in self.groups if g.slideshows), None)

    def step_slideshow(self, step: int) -> bool:
        """Next (+1) or previous (-1) individual slideshow in the group, wrapping around. From a
        group or Global, it goes to the group's last used (or first) slideshow. False if none."""
        group = self._current_group()
        if group is None:
            return False
        items = group.slideshows
        current = next((i for i, s in enumerate(items) if s.id == self._slideshow_id), None)
        if current is None:
            target = items[0]
        elif self._kind != SLIDESHOW:
            target = items[current]
        else:
            target = items[(current + step) % len(items)]
        self.select_slideshow(target)
        return True

    def step_group(self, step: int) -> bool:
        """Next (+1) or previous (-1) group with slideshows, wrapping around; shows its first
        individual slideshow. False if there's none."""
        groups = [g for g in self.groups if g.slideshows]
        if not groups:
            return False
        current = next((i for i, g in enumerate(groups) if g.id == self._group_id), None)
        target = groups[0] if current is None else groups[(current + step) % len(groups)]
        self.select_slideshow(target.slideshows[0])
        return True

    def show_current_group(self) -> bool:
        """Show the whole group the shortcuts are working in. False if there's none."""
        group = self._current_group()
        if group is None:
            return False
        self.select_group(group)
        return True

    # ---- groups and slideshows ----

    def _add_group(self, name) -> Group:
        group = Group(_new_id(), name)
        self.groups.append(group)
        return group

    def _add_slideshow(self, group, name) -> Slideshow:
        slideshow = Slideshow(_new_id(), name, group)
        group.slideshows.append(slideshow)
        return slideshow

    def next_name(self, base: str, names) -> str:
        """'Group 3' when 'Group 1' and 'Group 2' are taken, and so on."""
        taken = {n.casefold() for n in names}
        n = 1
        while f"{base} {n}".casefold() in taken:
            n += 1
        return f"{base} {n}"

    def create_group(self, name: str) -> Group:
        """Add a group (at the end)."""
        group = self._add_group(name)
        self._changed()
        return group

    def rename_group(self, group: Group, name: str):
        group.name = name
        self._changed()

    def delete_group(self, group: Group):
        """Deletes the group, its slideshows and their copies of the images and videos."""
        for slideshow in group.slideshows:
            self._remove_folder(slideshow)
        self.groups.remove(group)
        self._after_delete()

    def create_slideshow(self, group: Group, name: str) -> Slideshow:
        """Add an empty slideshow at the end of a group."""
        slideshow = self._add_slideshow(group, name)
        self.folder(slideshow).mkdir(parents=True, exist_ok=True)
        self._changed()
        return slideshow

    def rename_slideshow(self, slideshow: Slideshow, name: str):
        slideshow.name = name
        self._changed()

    def delete_slideshow(self, slideshow: Slideshow):
        """Deletes the slideshow and its copies of the images and videos."""
        self._remove_folder(slideshow)
        slideshow.group.slideshows.remove(slideshow)
        self._after_delete()

    def _after_delete(self):
        """After deleting: fall back to Global if what was showing is gone, then save and notify."""
        before = (self._kind, self._group_id, self._slideshow_id)
        self._check_selection()
        self._changed(selection=(self._kind, self._group_id, self._slideshow_id) != before)

    def _remove_folder(self, slideshow: Slideshow):
        """Delete a slideshow's folder of copied files (never anything outside the slideshows folder)."""
        folder = self.folder(slideshow)
        if folder.parent == self.root and folder.is_dir():  # only ever a slideshow's own folder
            shutil.rmtree(folder, ignore_errors=True)

    # ---- images and videos ----

    def add_media(self, slideshow: Slideshow, paths, done=None):
        """Copy readable images and videos into the slideshow, after its existing ones. Copying runs
        in the background (videos can be large); done(added, skipped) is called on the main thread."""
        folder = self.folder(slideshow)
        start = max((int(p.name[:4]) for p in self.media(slideshow) if p.name[:4].isdigit()), default=0)

        def work():
            """Background thread: validate and copy each file with the next number."""
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
                """Main thread: report back (and clean up if the slideshow was deleted meanwhile)."""
                if self.find_slideshow(slideshow.id) is None:  # deleted while copying
                    shutil.rmtree(folder, ignore_errors=True)
                elif added:
                    self._notify()
                if done is not None:
                    done(added, skipped)
                return False
            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def remove_media(self, path: Path):
        """Delete one image or video from its slideshow."""
        Path(path).unlink(missing_ok=True)
        self._notify()

    def clear(self, slideshow: Slideshow):
        """Delete all of a slideshow's images and videos."""
        for path in self.media(slideshow):
            path.unlink(missing_ok=True)
        self._notify()
