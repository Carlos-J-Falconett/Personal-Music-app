"""Stats tab: what you've listened to, from the history stats.py records.

Top to bottom:
- a period switch (Week / Month / Year / All time, rolling);
- four cards for that period: time listened, plays, different songs, different artists;
- a year of days as a heatmap (YearHeatmap), with a summary (days listened, current and longest
  streak), hover tooltips and arrows to move a year back or forward;
- the most listened artists, albums or songs of the period. Clicking one opens the artist or album,
  or plays the songs of the ranking from that one.

The page reads the database only while it's showing: when it appears, and when the recorder saves
something meanwhile."""

import datetime as dt
import math

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, GLib, Gtk, PangoCairo  # noqa: E402

from ..stats import PERIODS, format_listened, period_start, streaks  # noqa: E402
from .widgets import follow_compact  # noqa: E402

RANKINGS = (("artists", "Artists"), ("albums", "Albums"), ("songs", "Songs"))
RANKING_SIZE = 50


class StatsView(Adw.Bin):
    """The Stats tab (see the module docstring), a glass card like the other pages."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.db = win.app.db
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                         margin_start=24, margin_end=24, margin_bottom=24)

        # ---- header ----
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("page-header")
        title = Gtk.Label(label="Stats", xalign=0)
        title.add_css_class("page-title")
        self.since_label = Gtk.Label(xalign=0)
        self.since_label.add_css_class("page-subtitle")
        header.append(title)
        header.append(self.since_label)

        # ---- period and its numbers ----
        self.periods = _toggles(PERIODS)
        saved = self.db.get_setting("stats-period", "week")
        self.periods.set_active_name(saved if saved in dict((k, 1) for k, *_ in PERIODS) else "week")
        self.periods.connect("notify::active-name", lambda *_: self._on_period_changed())
        column.append(self.periods)
        self.cards = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        self.card_values = {}
        for key, label in (("listened", "Listened"), ("plays", "Plays"), ("songs", "Songs"), ("artists", "Artists")):
            self.card_values[key] = self._card(label)
        column.append(self.cards)
        follow_compact(win, self, StatsView._apply_compact)

        # ---- the year of days ----
        heat_header = Gtk.Box(spacing=6, margin_top=8)
        heat_titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.year_title = Gtk.Label(xalign=0)
        self.year_title.add_css_class("stats-section-title")
        self.year_summary = Gtk.Label(xalign=0, wrap=True)
        self.year_summary.add_css_class("stats-summary")
        heat_titles.append(self.year_title)
        heat_titles.append(self.year_summary)
        heat_header.append(heat_titles)
        self.older = _arrow("go-previous-symbolic", "Previous Year", lambda: self._move_year(1))
        self.newer = _arrow("go-next-symbolic", "Next Year", lambda: self._move_year(-1))
        heat_header.append(self.older)
        heat_header.append(self.newer)
        column.append(heat_header)
        self.heatmap = YearHeatmap(win.app.themes)
        # Narrow windows: the heatmap keeps readable squares and scrolls sideways instead.
        self.heat_scroller = Gtk.ScrolledWindow(child=self.heatmap, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                                propagate_natural_height=True)
        # It shows the most recent weeks until you scroll it yourself (layout may change its size
        # several times, so it keeps to the end until then).
        self._stick_to_end = True
        self._snap_source = 0
        self.heat_scroller.get_hadjustment().connect("changed", self._on_heat_scroll_range)
        for controller in (Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.HORIZONTAL
                                                     | Gtk.EventControllerScrollFlags.VERTICAL),
                           Gtk.GestureDrag()):
            controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            controller.connect("scroll" if isinstance(controller, Gtk.EventControllerScroll) else "drag-begin",
                               self._on_heat_user_scroll)
            self.heat_scroller.add_controller(controller)
        column.append(self.heat_scroller)
        self._years_back = 0  # 0 = the year up to today

        # ---- ranking ----
        self.rankings = _toggles(RANKINGS, margin_top=8)
        self.rankings.connect("notify::active-name", lambda *_: self._refresh_ranking())
        column.append(self.rankings)
        self.ranking = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.ranking.add_css_class("stats-ranking")
        self.ranking.connect("row-activated", lambda _l, row: self._open(row))
        self.no_ranking = Gtk.Label(label="Nothing played in this period", margin_top=12, margin_bottom=12)
        self.no_ranking.add_css_class("stats-summary")
        column.append(self.ranking)
        column.append(self.no_ranking)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(child=column, maximum_size=900))
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(header)
        page.append(scroller)
        page.add_css_class("content-glass")
        page.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.set_child(page)

        # Only read the database while showing: when shown, and when new listening is saved.
        self.connect("map", lambda *_: self.refresh())
        win.app.stats.connect("changed", lambda *_: self.refresh() if self.get_mapped() else None)

    # ---- building ----

    def _card(self, label: str) -> Gtk.Label:
        """A number card (placed by _apply_compact); returns its value label."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        card.add_css_class("stats-card")
        title = Gtk.Label(label=label, xalign=0)
        title.add_css_class("stats-card-label")
        value = Gtk.Label(label="0", xalign=0, ellipsize=3)
        value.add_css_class("stats-card-value")
        card.append(title)
        card.append(value)
        value._card = card
        return value

    @staticmethod
    def _apply_compact(view, level):
        """The cards two by two, or one under another on phone-sized windows."""
        per_row = 1 if level == 2 else 2
        for index, value in enumerate(view.card_values.values()):
            card = value._card
            if card.get_parent() is view.cards:
                view.cards.remove(card)
            view.cards.attach(card, index % per_row, index // per_row, 1, 1)

    # ---- data ----

    def _period_days(self):
        """Days back of the chosen period (None: all time)."""
        return dict((k, days) for k, _label, days in PERIODS)[self.periods.get_active_name() or "week"]

    def _on_period_changed(self):
        """Remember the period and show its numbers and ranking."""
        self.db.set_setting("stats-period", self.periods.get_active_name())
        self._refresh_period()
        self._refresh_ranking()

    def refresh(self):
        """Reload everything from the history."""
        first = self.db.first_play()
        self.since_label.set_text(f"Listening since {_day_text(dt.date.fromtimestamp(first), weekday=False)}"
                                  if first else "Nothing played yet: your listening shows up here as you play")
        self._refresh_period()
        self._refresh_year()
        self._refresh_ranking()

    def _refresh_period(self):
        """The four numbers for the chosen period."""
        seconds, plays, songs, artists = self.db.play_totals(period_start(self._period_days()))
        values = {"listened": format_listened(seconds), "plays": f"{plays:,}",
                  "songs": f"{songs:,}", "artists": f"{artists:,}"}
        for key, label in self.card_values.items():
            label.set_text(values[key])

    def _move_year(self, step: int):
        """‹ shows the year before, › the year after (up to the year ending today)."""
        self._years_back = max(0, self._years_back + step)
        self._refresh_year()

    def _refresh_year(self):
        """The heatmap, its title, summary and arrows, for the year being shown."""
        today = dt.date.today()
        end = today - dt.timedelta(days=365 * self._years_back)
        start = self.heatmap.show_year(end, self.db)
        self._stick_to_end = True
        self._on_heat_scroll_range(self.heat_scroller.get_hadjustment())
        days = self.heatmap.days
        current, longest = streaks(self.db.listening_days(), today)
        self.year_title.set_text("Past year" if self._years_back == 0
                                 else f"{start:%b %Y} – {end:%b %Y}")
        self.year_summary.set_text(f"{len(days)} day{'s' if len(days) != 1 else ''} listened · "
                                   f"{current}-day streak · longest {longest} day{'s' if longest != 1 else ''}")
        first = self.db.first_play()
        self.older.set_sensitive(first is not None and dt.date.fromtimestamp(first) < start)
        self.newer.set_sensitive(self._years_back > 0)

    def _on_heat_scroll_range(self, adjustment):
        """The heatmap's scroll range changed: keep showing its end (the most recent weeks).
        The range changes during layout, when a new value wouldn't be applied: so it's set just after."""
        if self._stick_to_end and not self._snap_source:
            def snap():
                self._snap_source = 0
                if self._stick_to_end:
                    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                return False
            self._snap_source = GLib.idle_add(snap)

    def _on_heat_user_scroll(self, *_):
        """You scrolled the heatmap: leave it where you put it."""
        self._stick_to_end = False
        return False

    def _refresh_ranking(self):
        """The top artists, albums or songs of the chosen period."""
        kind = self.rankings.get_active_name() or "artists"
        rows = self.db.play_top(kind, period_start(self._period_days()), RANKING_SIZE)
        self.ranking.remove_all()
        self._ranked = [(kind, r) for r in rows]
        for index, row in enumerate(rows):
            self.ranking.append(self._rank_row(index, kind, row))
        self.ranking.set_visible(bool(rows))
        self.no_ranking.set_visible(not rows)

    def _rank_row(self, index: int, kind: str, row) -> Gtk.ListBoxRow:
        """A ranking row: position, name (with the artist for albums and songs) and time listened."""
        box = Gtk.Box(spacing=14)
        rank = Gtk.Label(label=str(index + 1), width_chars=3, xalign=0)
        rank.add_css_class("stats-rank")
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label={"artists": row["artist"], "albums": row["album"], "songs": row["title"]}[kind],
                         xalign=0, ellipsize=3)
        name.add_css_class("stats-name")
        names.append(name)
        if kind != "artists":
            detail = Gtk.Label(label=row["artist"], xalign=0, ellipsize=3)
            detail.add_css_class("stats-detail")
            detail.add_css_class("caption")
            names.append(detail)
        time_label = Gtk.Label(label=format_listened(row["seconds"]))
        time_label.add_css_class("stats-time")
        for widget in (rank, names, time_label):
            box.append(widget)
        item = Gtk.ListBoxRow(child=box, activatable=True)
        item.add_css_class("stats-row")
        if index % 2 == 0:
            item.add_css_class("stats-row-alt")
        item._index = index
        return item

    def _open(self, item):
        """A ranking row was clicked: open the artist or album, or play the ranked songs from it."""
        kind, row = self._ranked[item._index]
        library = self.win.library
        if kind == "artists":
            if library.artist_tracks(row["artist"]):
                self.win.show_artist(row["artist"])
                return
        elif kind == "albums":
            if library.find_album((row["artist"], row["album"])) is not None:
                self.win.show_album((row["artist"], row["album"]))
                return
        else:
            tracks = [library.get(r["track_id"]) for _k, r in self._ranked]
            if tracks[item._index] is not None:
                playable = [t for t in tracks if t is not None]
                self.win.player.play_tracks(playable, playable.index(tracks[item._index]))
                return
        self.win.toast("It's not in your library any more")


class YearHeatmap(Gtk.DrawingArea):
    """A year of days as squares, one column per week (Sunday at the top), shaded by how long you
    listened that day. Month names above, Sun / Tue / Thu / Sat on the left; hovering a day tells
    its date, time listened and plays."""

    CELL = 14  # square and gap, at most (smaller when the page is narrow)...
    MIN_CELL = 10  # ...down to this; below it the heatmap scrolls sideways
    GAP = 3
    LEFT = 34  # room for the weekday names
    TOP = 18  # room for the month names
    WEEKS = 53
    # Minutes listened from which a day gets each shade (the empty colour below the first).
    LEVELS = (0, 15, 45, 90)

    def __init__(self, themes):
        super().__init__(hexpand=True, has_tooltip=True, content_height=self.TOP + 7 * self.CELL,
                         content_width=self.LEFT + self.WEEKS * self.MIN_CELL)
        self.add_css_class("stats-heatmap")
        self.themes = themes
        self.days: dict[str, tuple[float, int]] = {}  # 'YYYY-MM-DD' → (seconds, plays)
        self._first = dt.date.today()  # the Sunday the first column starts on
        self._end = dt.date.today()  # the last day shown
        self._pitch = self.CELL
        self._x0 = self.LEFT
        self.set_draw_func(self._draw)
        self.connect("query-tooltip", self._on_tooltip)
        themes.listeners.append(self.queue_draw)

    def show_year(self, end: dt.date, db) -> dt.date:
        """Show the WEEKS weeks ending with `end`, with their listening from the database.
        Returns the first day shown."""
        self._end = end
        # The last column is end's week; columns start on Sundays.
        last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
        self._first = last_sunday - dt.timedelta(weeks=self.WEEKS - 1)
        start_ts = dt.datetime.combine(self._first, dt.time()).timestamp()
        end_ts = dt.datetime.combine(end + dt.timedelta(days=1), dt.time()).timestamp()
        self.days = db.play_days(start_ts, end_ts)
        self.queue_draw()
        return self._first

    def _day_at(self, column: int, row: int) -> dt.date:
        """The date of the square in this column (week) and row (0 = Sunday)."""
        return self._first + dt.timedelta(days=column * 7 + row)

    def _draw(self, _area, cr, width, _height):
        """Draw the weekday and month names, then a square per day."""
        self._pitch = min(self.CELL, (width - self.LEFT) / self.WEEKS)
        self._x0 = self.LEFT
        size = max(2.0, self._pitch - self.GAP * self._pitch / self.CELL)
        text = self.get_color()
        cr.set_source_rgba(text.red, text.green, text.blue, 0.6)
        for row, name in ((0, "Sun"), (2, "Tue"), (4, "Thu"), (6, "Sat")):
            self._text(cr, name, 0, self.TOP + row * self._pitch + size / 2)
        # A month's name goes above its first column; one that would crowd the next (a month cut
        # off at the start of the year) is left out.
        starts, previous_month = [], None
        for column in range(self.WEEKS):
            month = self._day_at(column, 6).month
            if month != previous_month:
                starts.append(column)
            previous_month = month
        free_from = 0.0  # where the previous name ended
        for column, following in zip(starts, starts[1:] + [self.WEEKS + 3]):
            x = self._x0 + column * self._pitch
            if following - column >= 3 and x >= free_from:
                free_from = x + self._text(cr, f"{self._day_at(column, 6):%b}", x, self.TOP / 2 - 1) + 4
        heat = self.themes.color("stats_heatmap")
        empty = self.themes.color("stats_empty_day")
        radius = size / 4
        for column in range(self.WEEKS):
            for row in range(7):
                day = self._day_at(column, row)
                if day > self._end:
                    break
                seconds = self.days.get(day.isoformat(), (0, 0))[0]
                level = sum(seconds > m * 60 for m in self.LEVELS)  # 0 = nothing played
                if level:
                    cr.set_source_rgba(*heat, (0.3, 0.52, 0.76, 1.0)[level - 1])
                else:
                    cr.set_source_rgba(*empty, 1.0)
                _rounded(cr, self._x0 + column * self._pitch, self.TOP + row * self._pitch, size, radius)
                cr.fill()

    def _text(self, cr, text: str, x: float, middle_y: float) -> int:
        """Draw small text with its left edge at x, centred vertically on middle_y; returns its width."""
        layout = self.create_pango_layout(text)
        w, h = layout.get_pixel_size()
        cr.move_to(x, middle_y - h / 2)
        PangoCairo.show_layout(cr, layout)
        return w

    def _on_tooltip(self, _widget, x, y, _keyboard, tooltip):
        """Hovering a day: its date, and what was played."""
        column, row = int((x - self._x0) // self._pitch), int((y - self.TOP) // self._pitch)
        if not (0 <= column < self.WEEKS and 0 <= row < 7):
            return False
        day = self._day_at(column, row)
        if day > self._end:
            return False
        seconds, plays = self.days.get(day.isoformat(), (0, 0))
        detail = (f"{format_listened(seconds)} · {plays} play{'s' if plays != 1 else ''}"
                  if seconds else "Nothing played")
        tooltip.set_text(f"{_day_text(day)} · {detail}")
        return True


def _day_text(day: dt.date, weekday: bool = True) -> str:
    """'Sat 3 Jan 2026' (or without the weekday: '3 Jan 2026')."""
    return (f"{day:%a} " if weekday else "") + f"{day.day} {day:%b %Y}"


def _rounded(cr, x, y, size, radius):
    """A rounded square path."""
    cr.new_sub_path()
    for cx, cy, start in ((x + size - radius, y + radius, -90), (x + size - radius, y + size - radius, 0),
                          (x + radius, y + size - radius, 90), (x + radius, y + radius, 180)):
        cr.arc(cx, cy, radius, math.radians(start), math.radians(start + 90))
    cr.close_path()


def _toggles(options, **kwargs) -> Adw.ToggleGroup:
    """A pill switch of (name, label, …) options, in the tabs' style."""
    group = Adw.ToggleGroup(homogeneous=True, **kwargs)
    group.add_css_class("stats-toggles")
    for name, label, *_rest in options:
        group.add(Adw.Toggle(name=name, label=label))
    return group


def _arrow(icon: str, tooltip: str, callback) -> Gtk.Button:
    """A flat round arrow button for moving between years."""
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.add_css_class("circular")
    button.connect("clicked", lambda *_: callback())
    return button
