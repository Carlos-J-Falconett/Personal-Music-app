"""Audio-wave ribbon driven by the player's frequency spectrum (bass left, treble right).

Every frequency band is one lobe of the wave, alternating up and down, as tall as that band is
loud. Two edges (Wave Top above, Wave Bottom below) trace the lobes slightly out of step, so the
band between them twists like a ribbon, drawn as many fine lines blending one colour into the
other. At rest the edges are two parallel lines, which the lobes grow out of."""

import math

import cairo
from gi.repository import Gio, Gtk

from ..player import SPECTRUM_BANDS
from ..themes import mix

BINS = 64
F_LOW, F_HIGH = 50.0, 16000.0
NYQUIST = 22050.0  # most music is 44.1 kHz; close enough for 48 kHz files
TILT_DB_PER_OCTAVE = 3.0  # music has less energy up high; lift it so the right side moves too
# Loudness window mapped onto the wave's height: quiet detail below FLOOR is dropped,
# so peaks stand out instead of every bin sitting at mid height.
FLOOR_DB, CEILING_DB = -70.0, -10.0
CURVE = 1.4
ATTACK, DECAY = 0.5, 0.12
SETTLED = 0.002

# ---- look ----
TAIL = 0.12  # flat parallel lines on each side, as a fraction of the width
LOBE_PX = 11  # about one lobe (frequency band) per this many pixels of wave...
MIN_LOBES, MAX_LOBES = 24, BINS  # ...within these limits
GAP = 0.06  # the two resting lines are this far apart (of the height)
LAG = 0.35  # the bottom edge's lobes sit this far (of a lobe) to the right...
LAG_SCALE = 0.85  # ...at this much of the height...
DROP = 0.3  # ...and up to this much (of the height) lower, so the ribbon twists and crosses
LINES = 20
STROKE_TOLERANCE = 0.5  # px; cairo's default is 0.1
LINES_PER_STROKE = 3
# Automatic gain: the loudest recent level fills the height; it lets go slowly (per frame).
GAIN_RELEASE = 0.995
GAIN_FLOOR = 0.3  # never boost more than this allows, so quiet passages stay calm
RED = (1.0, 0.18, 0.39)  # #ff2e63
BLUE = (0.24, 0.55, 1.0)  # #3d8bff
RED_EDGE = (1.0, 0.56, 0.69)  # #ff8fb1
BLUE_EDGE = (0.56, 0.83, 1.0)  # #8fd3ff


def _bin_edges():
    """Frequency ranges of the BINS bins, spaced evenly on a log scale from F_LOW to F_HIGH."""
    ratio = (F_HIGH / F_LOW) ** (1 / BINS)
    return [(F_LOW * ratio ** i, F_LOW * ratio ** (i + 1)) for i in range(BINS)]


class WaveVisualizer(Gtk.DrawingArea):
    """The audio wave (see the module docstring). Only visible ones ask the player for the spectrum;
    levels ease toward each new result on the frame clock."""

    def __init__(self, player, height: int = 96, min_height: int | None = None):
        """height: the natural height; min_height: how far it may shrink when space is short."""
        super().__init__(hexpand=True)
        self._height, self._min_height = height, min_height or height
        self.add_css_class("visualizer")
        self.player = player
        self._levels = [0.0] * BINS
        self._targets = [0.0] * BINS
        self._tick_id = 0
        self._peak = GAIN_FLOOR
        self._band_width = NYQUIST / SPECTRUM_BANDS
        self._edges = _bin_edges()
        self.set_draw_func(self._draw)

        player.connect("spectrum", self._on_spectrum)
        player.connect("state-changed", lambda _p, playing: None if playing else self._fall())
        player.connect("track-changed", lambda *_: self._fall())
        # The main window keeps the spectrum coming while it's on screen (MainWindow), so a wave
        # hidden behind the lyrics, equalizer or queue keeps following the song, and is in step
        # the moment it's shown again. A visualizer on screen asks for it too.
        self.connect("map", lambda *_: (player.want_spectrum(True), self._catch_up()))
        self.connect("unmap", lambda *_: player.want_spectrum(False))
        self._themes = getattr(Gio.Application.get_default(), "themes", None)
        if self._themes is not None:
            self._themes.listeners.append(self.queue_draw)

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return self._min_height, self._height, -1, -1
        return 0, 0, -1, -1

    # ---- data ----

    def _on_spectrum(self, _player, magnitudes):
        """A new spectrum result: map the bands onto BINS log-spaced bins (0..1) as targets.
        Also while hidden (it's cheap), so the wave is up to date when it's shown again."""
        span = len(magnitudes)
        for i, (lo, hi) in enumerate(self._edges):
            a, b = lo / self._band_width, hi / self._band_width
            if b - a < 1:
                # Narrow (bass) bin: interpolate between neighbouring bands.
                x = min(max((a + b) / 2 - 0.5, 0), span - 1)
                j = int(x)
                frac = x - j
                db = magnitudes[j] * (1 - frac) + magnitudes[min(j + 1, span - 1)] * frac
            else:
                db = max(magnitudes[int(a):min(math.ceil(b), span)])
            center = math.sqrt(lo * hi)
            db += TILT_DB_PER_OCTAVE * math.log2(center / 1000)
            level = (db - FLOOR_DB) / (CEILING_DB - FLOOR_DB)
            self._targets[i] = min(max(level, 0.0), 1.0) ** CURVE
        self._animate()

    def _fall(self):
        """Paused or a new song: let the wave settle to its resting lines."""
        self._targets = [0.0] * BINS
        self._animate()

    def _catch_up(self):
        """Shown again: jump straight to the current levels (no rise from the resting lines)."""
        self._levels = list(self._targets)
        self._peak = max(self._peak, max(self._levels), GAIN_FLOOR)
        self.queue_draw()
        self._animate()

    def _animate(self):
        """Make sure the frame-clock animation is running (only on screen: hidden, it has no frames)."""
        if not self._tick_id and self.get_mapped():
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget, _clock):
        """Per frame: move levels toward their targets (fast up, slow down); stop when settled at rest."""
        self._peak = max(max(self._levels), self._peak * GAIN_RELEASE, GAIN_FLOOR)
        moving = False
        for i, target in enumerate(self._targets):
            level = self._levels[i]
            level += (target - level) * (ATTACK if target > level else DECAY)
            self._levels[i] = level
            if abs(target - level) > SETTLED:
                moving = True
        self.queue_draw()
        if not moving and not any(self._targets):
            self._levels = [0.0] * BINS
            self._tick_id = 0
            return False
        return True

    # ---- drawing ----

    def _lobe_levels(self, n):
        """Loudness of n equal groups of bins (bass first), 0..1 after automatic gain."""
        levels = []
        for k in range(n):
            a, b = k * BINS // n, max((k + 1) * BINS // n, k * BINS // n + 1)
            levels.append(min(max(self._levels[a:b]) / self._peak, 1.0))
        return levels

    def _edge(self, x0, x1, width, base, levels, amp, drop, shift, scale):
        """One edge's spline points from x0 to x1 (the flat tails are drawn straight by _trace):
        one lobe per band, alternating up and down, starting and ending at `base`."""
        n = len(levels)
        span = x1 - x0
        pts = [(x0, base)]
        for k, level in enumerate(levels):
            u = (k + 0.5 + shift) / n
            if u >= 1:
                break
            window = math.sin(math.pi * u) ** 0.6  # lobes ease in and out of the flat ends
            sign = 1 if k % 2 == 0 else -1
            pts.append((x0 + u * span, base - sign * amp * scale * window * level + drop * window * level))
        pts.append((x1, base))
        return pts

    def _palette(self):
        """Top, bottom and their bright edge colours: the built-in ones, or the colour theme's."""
        if self._themes is None or not self._themes.is_custom:
            return RED, BLUE, RED_EDGE, BLUE_EDGE
        colors = self._themes.colors()
        top, bottom = colors["wave_top"], colors["wave_bottom"]
        return top, bottom, mix(top, (1, 1, 1), 0.45), mix(bottom, (1, 1, 1), 0.45)

    def _draw(self, _area, cr, width, height):
        """Draw the ribbon of fine lines between the two edges, then the edges themselves."""
        mid = height / 2
        cr.set_line_cap(cairo.LineCap.ROUND)
        # The curves are smooth, so bevel joins and a coarser flattening tolerance look the same
        # but make stroking the many ribbon lines several times cheaper.
        cr.set_line_join(cairo.LineJoin.BEVEL)
        cr.set_tolerance(STROKE_TOLERANCE)
        red, blue, red_edge, blue_edge = self._palette()

        x0, x1 = width * TAIL, width * (1 - TAIL)
        n = min(max(round((x1 - x0) / LOBE_PX), MIN_LOBES), MAX_LOBES)
        levels = self._lobe_levels(n)
        loud = min(max(levels) * 1.2, 1.0)  # 0 at rest: the ribbon fades down to its two edges
        gap = max(height * GAP, 4.0)
        drop = height * DROP
        amp = mid - gap / 2 - drop / 2 - 3  # the lobes use the full height without leaving it
        top = self._edge(x0, x1, width, mid - gap / 2, levels, amp, -drop / 2, 0.0, 1.0)
        bottom = self._edge(x0, x1, width, mid + gap / 2, levels, amp, drop / 2, LAG, LAG_SCALE)
        if len(bottom) < len(top):  # the shifted edge can lose its last lobe; keep pairs aligned
            top = top[:len(bottom) - 2] + top[-2:]

        # The ribbon: fine lines blending from the top edge to the bottom edge.
        if loud > 0.01:
            cr.set_line_width(0.7)
            cr.set_antialias(cairo.Antialias.FAST)  # fine inner lines: looks the same, half the cost
            inner = list(range(1, LINES - 1))
            for group in (inner[j:j + LINES_PER_STROKE] for j in range(0, len(inner), LINES_PER_STROKE)):
                # Neighbouring lines share one stroke (and its blended colour): far cheaper to draw.
                t_mid = (group[0] + group[-1]) / 2 / (LINES - 1)
                r, g, b = (red[c] + (blue[c] - red[c]) * t_mid for c in range(3))
                cr.set_source_rgba(r, g, b, 0.8 * loud)
                for i in group:
                    t = i / (LINES - 1)
                    pts = [(ax + (bx - ax) * t, ay + (by - ay) * t) for (ax, ay), (bx, by) in zip(top, bottom)]
                    _trace(cr, pts, width)
                cr.stroke()
            cr.set_antialias(cairo.Antialias.DEFAULT)

        # The two edges: exactly the wave colours at rest, brighter and glowing with the music.
        for pts, base, edge in ((bottom, blue, blue_edge), (top, red, red_edge)):
            if loud > 0.01:
                cr.set_line_width(4)
                cr.set_source_rgba(*base, 0.25 * loud)
                _trace(cr, pts, width)
                cr.stroke()
            cr.set_line_width(1.4)
            cr.set_source_rgba(*mix(base, edge, loud), 0.95)
            _trace(cr, pts, width)
            cr.stroke()


def _trace(cr, pts, width):
    """Flat tail from the left edge, the smooth wave through pts, flat tail to the right edge."""
    cr.move_to(0, pts[0][1])
    cr.line_to(*pts[0])
    _curve_through(cr, pts)
    cr.line_to(width, pts[-1][1])


def _curve_through(cr, pts):
    """Smooth Catmull-Rom spline through pts (current point must be pts[0])."""
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        cr.curve_to(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
