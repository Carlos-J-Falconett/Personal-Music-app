"""A two-sided card that flips around its vertical axis with a 3D perspective animation."""

import math

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402

FLIP_MS = 450


class FlipCard(Gtk.Widget):
    """A card with a front and a back that flips over (set_flipped), and whose back can do a half turn
    onto new content (turn). Only the side facing the viewer is drawn and receives input."""
    __gtype_name__ = "FlipCard"

    def __init__(self, front: Gtk.Widget, back: Gtk.Widget):
        super().__init__()
        self.set_overflow(Gtk.Overflow.VISIBLE)  # the rotating card may poke out of its box
        self.front, self.back = front, back
        front.set_parent(self)
        back.set_parent(self)
        self._progress = 0.0  # 0 = front showing, 1 = back showing
        self._flipped = False
        target = Adw.CallbackAnimationTarget.new(self._set_progress)
        self._animation = Adw.TimedAnimation.new(self, 0.0, 1.0, FLIP_MS, target)
        self._animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        # A half-turn of the back side onto new content (e.g. lyrics → equalizer).
        self._turn = 0.0
        self._turning = False
        self._turn_swap = None
        self._turn_animation = Adw.TimedAnimation.new(self, 0.0, 1.0, FLIP_MS,
                                                      Adw.CallbackAnimationTarget.new(self._set_turn))
        self._turn_animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._turn_animation.connect("done", lambda *_: self._end_turn())
        self._sync_sides()

    @property
    def flipped(self) -> bool:
        """Whether the back is (or is turning to be) showing."""
        return self._flipped

    def set_flipped(self, flipped: bool, animate: bool = True):
        """Flip to the back (True) or the front, animated unless `animate` is False or it's hidden."""
        if flipped == self._flipped:
            return
        self._flipped = flipped
        self.queue_resize()  # the minimum size follows the side that's showing
        self._animation.pause()
        target = 1.0 if flipped else 0.0
        if not animate or not self.get_mapped():
            self._set_progress(target)
            return
        self._animation.set_value_from(self._progress)
        self._animation.set_value_to(target)
        # A partly finished flip reverses in proportionally less time.
        self._animation.set_duration(max(1, int(FLIP_MS * abs(target - self._progress))))
        self._animation.play()

    def turn(self, swap):
        """While the back is showing, spin it a half-turn and call `swap()` at the edge-on
        midpoint so the new back content rotates into view."""
        if self._turning:
            self._turn_animation.skip()  # finish the running turn (runs its swap)
        if not self._flipped or not self.get_mapped():
            swap()
            return
        self._turning = True
        self._turn_swap = swap
        self._turn = 0.0
        self._turn_animation.play()

    def finish_turn(self):
        """Jump a running turn to its end (its swap runs), so a direct swap after it sticks."""
        if self._turning:
            self._turn_animation.skip()

    def _set_turn(self, value: float):
        """Animation step of a turn; the content is swapped when the card is edge-on."""
        self._turn = value
        if value >= 0.5 and self._turn_swap is not None:
            swap, self._turn_swap = self._turn_swap, None
            swap()
        self.queue_draw()

    def _end_turn(self):
        """A turn finished (or was skipped): make sure the swap happened."""
        if self._turn_swap is not None:
            swap, self._turn_swap = self._turn_swap, None
            swap()
        self._turning = False
        self._turn = 0.0
        self.queue_draw()

    def _set_progress(self, value: float):
        """Animation step of a flip (0 = front, 1 = back)."""
        self._progress = value
        self._sync_sides()
        self.queue_draw()

    def _sync_sides(self):
        """Only the side facing the viewer is visible, focusable and clickable."""
        back_up = self._progress >= 0.5
        if self.back.get_child_visible() == back_up and self.front.get_child_visible() != back_up:
            return
        # Only the side facing the viewer is drawn, focusable and clickable.
        self.front.set_child_visible(not back_up)
        self.back.set_child_visible(back_up)
        # The newly shown side must be laid out before it's drawn this frame.
        self.queue_allocate()

    # ---- layout ----

    def do_measure(self, orientation, for_size):
        # The minimum is the showing (or about to show) side's, so a big back side doesn't stop
        # a small front from shrinking; the natural size fits either.
        nats = []
        for child in (self.front, self.back):
            nats.append(child.measure(orientation, for_size)[1])
        minimum = (self.back if self._flipped else self.front).measure(orientation, for_size)[0]
        return minimum, max(max(nats), minimum), -1, -1

    def do_size_allocate(self, width, height, baseline):
        # Both sides get the card's size, but never less than their own minimum.
        for child in (self.front, self.back):
            w = max(width, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
            h = max(height, child.measure(Gtk.Orientation.VERTICAL, w)[0])
            child.allocate(w, h, baseline, None)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if self._turning and self._flipped:
            # Old back content turns away (0→90°), new content turns in (-90→0°).
            child, t = self.back, self._turn
            face_angle = 180.0 * t if t < 0.5 else 180.0 * t - 180.0
        else:
            t = self._progress
            angle = t * 180.0
            showing_back = angle > 90.0
            child = self.back if showing_back else self.front
            if angle in (0.0, 180.0):
                self.snapshot_child(child, snapshot)
                return
            # Seen from the back, the face must turn a further 180° to read the right way round.
            face_angle = angle - 180.0 if showing_back else angle
        depth = 1.0 - 0.06 * math.sin(math.pi * t)  # slight dip mid-flip
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.perspective(max(width, height) * 2.5)
        snapshot.rotate_3d(face_angle, Graphene.Vec3.y_axis())
        snapshot.scale(depth, depth)
        snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
        self.snapshot_child(child, snapshot)
        snapshot.restore()

    def do_dispose(self):
        for child in (self.front, self.back):
            if child is not None and child.get_parent() is self:
                child.unparent()
        self.front = self.back = None
        Gtk.Widget.do_dispose(self)
