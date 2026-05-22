#!/usr/bin/env python3
"""KBO Bar – macOS menu bar app showing KBO live scores via rumps."""

import json
import queue
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import rumps

from kbo_scraper import get_today_games

KST = ZoneInfo("Asia/Seoul")

STATUS_PREGAME   = "1"
STATUS_LIVE      = "2"
STATUS_FINAL     = "3"
STATUS_CANCELLED = "4"
STATUS_SUSPENDED = "5"

_STATUS_LABEL = {
    STATUS_PREGAME:   "경기전",
    STATUS_LIVE:      "진행중",
    STATUS_FINAL:     "종료",
    STATUS_CANCELLED: "우천취소",
    STATUS_SUSPENDED: "서스펜디드",
}

ICON_BASE  = "⚾"
ICON_ERROR = "⚠"

CONFIG_PATH = Path.home() / ".kbobar" / "config.json"
KBO_TEAMS   = ["NC", "두산", "LG", "KIA", "SSG", "키움", "롯데", "한화", "KT", "삼성"]
TEAM_ICONS  = {
    "NC":   "🦖",
    "두산":  "🐻",
    "LG":   "👯",
    "KIA":  "🐯",
    "SSG":  "🚀",
    "키움":  "🦸",
    "롯데":  "🗿",
    "한화":  "🦅",
    "KT":   "🧙‍♂️",
    "삼성":  "🦁",
}


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def _status_label(g: dict) -> str:
    """Human-readable status string; live games show current inning."""
    s = g.get("status", "?")
    if s == STATUS_LIVE:
        inn  = g.get("inning")
        half = g.get("inning_half")
        if inn:
            arrow = "▲" if half == "초" else "▼" if half == "말" else ""
            return f"{inn}{arrow}"
    if s == STATUS_CANCELLED:
        return g.get("cancel_reason") or "취소"
    return _STATUS_LABEL.get(s, s)


def _base_str(g: dict) -> str:
    """Diamond notation for base runners: {3rd}{2nd}{1st}. Empty string if unknown."""
    b1, b2, b3 = g.get("base1"), g.get("base2"), g.get("base3")
    if b1 is None:
        return ""
    return ("◆" if b3 else "◇") + ("◆" if b2 else "◇") + ("◆" if b1 else "◇")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_game_time(time_str: str) -> datetime | None:
    try:
        h, m = map(int, time_str.split(":"))
        today = _now_kst().date()
        return datetime(today.year, today.month, today.day, h, m, tzinfo=KST)
    except Exception:
        return None


def _poll_interval(games: list[dict], game_details: dict | None = None) -> int:
    if not games:
        return 3600

    statuses = [g["status"] for g in games]
    if all(s in (STATUS_FINAL, STATUS_CANCELLED, STATUS_SUSPENDED) for s in statuses):
        if game_details and any(
            g["status"] == STATUS_FINAL and game_details.get(g["game_id"]) is None
            for g in games
        ):
            return 30
        return 600

    if any(g["status"] == STATUS_LIVE for g in games):
        return 10

    now = _now_kst()
    start_times = [_parse_game_time(g["time"]) for g in games if g["time"]]
    start_times = [t for t in start_times if t]
    if not start_times:
        return 600

    first_start       = min(start_times)
    last_expected_end = max(start_times) + timedelta(hours=3, minutes=30)

    if first_start - timedelta(minutes=30) <= now <= last_expected_end + timedelta(minutes=30):
        return 30

    return 600


def _linescore_menu_lines(detail: dict) -> list[str]:
    """Inning-by-inning grid: header row, away row, home row, separator."""
    away_inn = detail.get("away_innings", [])
    home_inn = detail.get("home_innings", [])

    n = max(len(away_inn), len(home_inn), 9)

    def _dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    def _v(x) -> str:
        return str(x) if x is not None else "-"

    def fmt_row(name: str, scores: list[str], r, h, e, b) -> str:
        pad = " " * max(0, 5 - _dw(name))
        padded = list(scores[:n]) + ["-"] * max(0, n - len(scores))
        cells = " ".join(f"{s:>2}" for s in padded)
        return f"{name}{pad} {cells} |{_v(r)}  {_v(h)}  {_v(e)}  {_v(b)}"

    away = detail.get("away", "?")
    home = detail.get("home", "?")
    header  = "      " + " ".join(f"{i+1:>2}" for i in range(n)) + " |R  H  E  B"
    away_ln = fmt_row(away[:5], away_inn,
                      detail.get("away_score"), detail.get("away_hits"),
                      detail.get("away_errors"), detail.get("away_bb"))
    home_ln = fmt_row(home[:5], home_inn,
                      detail.get("home_score"), detail.get("home_hits"),
                      detail.get("home_errors"), detail.get("home_bb"))

    return [header, away_ln, home_ln]


class KBOBarApp(rumps.App):
    def __init__(self):
        super().__init__(ICON_BASE, quit_button=None)

        self._games: list[dict] = []
        self._error: bool       = False
        self._cycle_index: int  = 0
        self._last_date: str | None = None
        self._lock = threading.Lock()
        cfg = _load_config()
        self._favorite:   str | None = cfg.get("favorite_team") or None
        self._team_icon:  bool       = bool(cfg.get("team_icon", False))
        self._fav_detail:   dict | None              = None
        self._game_details: dict[str, dict | None]   = {}

        # All NSMenu mutations and other main-thread work go through this queue.
        # The drain timer fires every 100 ms on the main thread and runs them.
        self._main_queue: queue.Queue = queue.Queue()
        self._drain_timer = rumps.Timer(self._drain_main_queue, 0.1)
        self._drain_timer.start()

        self._cycle_timer = rumps.Timer(self._on_cycle_tick, 5)
        self._cycle_timer.start()

        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Main-thread dispatch
    # ------------------------------------------------------------------

    def _drain_main_queue(self, _sender):
        try:
            while True:
                fn = self._main_queue.get_nowait()
                fn()
        except queue.Empty:
            pass

    def _on_main_thread(self, fn):
        self._main_queue.put(fn)

    # ------------------------------------------------------------------
    # Polling loop (background thread)
    # ------------------------------------------------------------------

    def _poll_loop(self):
        while True:
            self._do_fetch()

            with self._lock:
                interval  = _poll_interval(self._games, self._game_details)
                last_date = self._last_date

            elapsed = 0
            while elapsed < interval:
                chunk = min(15, interval - elapsed)
                time.sleep(chunk)
                elapsed += chunk
                if last_date and _now_kst().strftime("%Y%m%d") != last_date:
                    break  # midnight crossed – reload schedule

    def _do_fetch(self):
        try:
            games = get_today_games()
            with self._lock:
                self._games     = games
                self._error     = False
                self._last_date = _now_kst().strftime("%Y%m%d")
                favorite        = self._favorite
        except Exception:
            with self._lock:
                self._error = True
            self._on_main_thread(self._rebuild_menu)
            return

        game_details: dict[str, dict | None] = {}
        for g in games:
            gid    = g["game_id"]
            status = g.get("status")
            if status in (STATUS_LIVE, STATUS_FINAL):
                innings = g.get("away_innings")
                game_details[gid] = {
                    "away":         g["away"],
                    "home":         g["home"],
                    "away_score":   g["away_score"],
                    "home_score":   g["home_score"],
                    "away_innings": innings or [],
                    "home_innings": g.get("home_innings") or [],
                    "away_hits":    g.get("away_hits"),
                    "home_hits":    g.get("home_hits"),
                    "away_errors":  g.get("away_errors"),
                    "home_errors":  g.get("home_errors"),
                    "away_bb":      g.get("away_bb"),
                    "home_bb":      g.get("home_bb"),
                } if isinstance(innings, list) else None

        fav_detail = None
        if favorite:
            fav_game = next(
                (g for g in games if favorite in (g.get("away"), g.get("home"))),
                None,
            )
            if fav_game:
                fav_detail = game_details.get(fav_game["game_id"])

        with self._lock:
            self._game_details = game_details
            self._fav_detail   = fav_detail

        self._on_main_thread(self._rebuild_menu)

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------

    def _icon_base(self) -> str:
        if self._team_icon and self._favorite:
            return TEAM_ICONS.get(self._favorite, ICON_BASE)
        return ICON_BASE

    def _compute_title(self) -> str:
        with self._lock:
            games = list(self._games)
            error = self._error
            idx   = self._cycle_index

        icon   = self._icon_base()
        suffix = f" {ICON_ERROR}" if error else ""

        if self._favorite:
            fav = next(
                (g for g in games if self._favorite in (g.get("away"), g.get("home"))),
                None,
            )
            if fav:
                status = fav.get("status")
                if status == STATUS_LIVE:
                    inn = _status_label(fav)
                    return f"{icon} {fav['away']} {fav['away_score']}-{fav['home_score']} {fav['home']}  {inn}{suffix}"
                if status == STATUS_CANCELLED:
                    reason = fav.get("cancel_reason")
                    cancel_str = f" ({reason})" if reason else ""
                    return f"{icon} {fav['away']} vs {fav['home']} 취소{cancel_str}{suffix}"
            return icon + suffix

        live = [g for g in games if g["status"] == STATUS_LIVE]
        if not live:
            return icon + suffix
        g = live[idx % len(live)]
        inn = _status_label(g)
        return f"{icon} {g['away']} {g['away_score']}-{g['home_score']} {g['home']}  {inn}{suffix}"

    def _on_cycle_tick(self, _sender):
        with self._lock:
            live = [g for g in self._games if g["status"] == STATUS_LIVE]
            if live:
                self._cycle_index = (self._cycle_index + 1) % len(live)
        self.title = self._compute_title()

    # ------------------------------------------------------------------
    # Menu construction (runs on main thread via _on_main_thread)
    # ------------------------------------------------------------------

    def _rebuild_menu(self):
        with self._lock:
            games        = list(self._games)
            favorite     = self._favorite
            fav_detail   = self._fav_detail
            game_details = dict(self._game_details)

        items: list = []

        fav_game = None
        if favorite:
            fav_game = next(
                (g for g in games if favorite in (g.get("away"), g.get("home"))),
                None,
            )

        if fav_game is not None:
            items.append(rumps.separator)
            items += self._make_inline_fav_items(fav_game, fav_detail)
            items.append(rumps.separator)

        other_games = [g for g in games if g is not fav_game]
        items += [self._make_game_item(g, game_details.get(g["game_id"])) for g in other_games]

        items += [
            rumps.separator,
            self._make_settings_item(),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self.menu.clear()
        self.menu.update(items)
        self.title = self._compute_title()

    def _make_inline_fav_items(self, g: dict, detail: dict | None) -> list:
        """Flat (non-expandable) block shown at the top of the menu for the favorite game."""
        status      = g.get("status")
        header_text = f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']}  [{_status_label(g)}]"

        # Live/final: give header a no-op callback so it renders active (black text).
        # Pre-game/cancelled: no callback — all items in the block are greyed uniformly.
        if status in (STATUS_LIVE, STATUS_FINAL):
            header = rumps.MenuItem(header_text, callback=lambda _: None)
        else:
            header = rumps.MenuItem(header_text)

        rows: list = [header]

        if status == STATUS_PREGAME:
            rows.append(rumps.MenuItem(f"{g.get('venue') or '?'}  {g.get('time') or '?'}"))
            away_s = g.get("away_starter") or ""
            home_s = g.get("home_starter") or ""
            if away_s or home_s:
                rows.append(rumps.MenuItem(f"{away_s or '?'} vs {home_s or '?'}"))
            ar = g.get("away_rank") or "?"
            hr = g.get("home_rank") or "?"
            rows.append(rumps.MenuItem(f"{g.get('away') or '?'} #{ar}  {g.get('home') or '?'} #{hr}"))

        elif status in (STATUS_LIVE, STATUS_FINAL):
            if detail:
                for line in _linescore_menu_lines(detail):
                    rows.append(rumps.MenuItem(line))
                rows.append(rumps.separator)

            if status == STATUS_LIVE:
                b     = g.get("balls")   or 0
                s     = g.get("strikes") or 0
                o     = g.get("outs")    or 0
                bases = _base_str(g)
                count = f"{bases}  B{b} S{s} O{o}" if bases else f"B{b} S{s} O{o}"
                rows.append(rumps.MenuItem(count))
                t_player = g.get("away_player") or "?"
                b_player = g.get("home_player") or "?"
                if g.get("inning_half") == "초":  # top: away batting, home pitching
                    pitcher, batter = b_player, t_player
                else:                             # bottom: home batting, away pitching
                    pitcher, batter = t_player, b_player
                rows.append(rumps.MenuItem(f"투수: {pitcher}  타자: {batter}"))

            elif status == STATUS_FINAL:
                if not detail:
                    rows.append(rumps.MenuItem("로딩 중..."))
                else:
                    wp = g.get("win_pitcher")
                    lp = g.get("loss_pitcher")
                    sp = g.get("save_pitcher")
                    if wp or lp:
                        parts = []
                        if wp: parts.append(f"승 {wp}")
                        if lp: parts.append(f"패 {lp}")
                        if sp: parts.append(f"세 {sp}")
                        rows.append(rumps.MenuItem("  ".join(parts)))

        return rows

    def _make_game_item(self, g: dict, detail: dict | None = None) -> rumps.MenuItem:
        label  = f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']}  [{_status_label(g)}]"
        item   = rumps.MenuItem(label)
        status = g.get("status")

        if status == STATUS_PREGAME:
            item.add(rumps.MenuItem(f"{g.get('venue') or '?'}  {g.get('time') or '?'}"))
            away_s = g.get("away_starter") or ""
            home_s = g.get("home_starter") or ""
            if away_s or home_s:
                item.add(rumps.MenuItem(f"{away_s or '?'} vs {home_s or '?'}"))
            ar = g.get("away_rank") or "?"
            hr = g.get("home_rank") or "?"
            item.add(rumps.MenuItem(f"{g.get('away') or '?'} #{ar}  {g.get('home') or '?'} #{hr}"))

        elif status in (STATUS_CANCELLED, STATUS_SUSPENDED):
            pass  # no submenu — item has no arrow

        elif status in (STATUS_LIVE, STATUS_FINAL):
            if detail:
                for line in _linescore_menu_lines(detail):
                    item.add(rumps.MenuItem(line))
                item.add(rumps.separator)

            if status == STATUS_LIVE:
                b     = g.get("balls")   or 0
                s     = g.get("strikes") or 0
                o     = g.get("outs")    or 0
                bases = _base_str(g)
                count = f"{bases}  B{b} S{s} O{o}" if bases else f"B{b} S{s} O{o}"
                item.add(rumps.MenuItem(count))
                t_player = g.get("away_player") or "?"
                b_player = g.get("home_player") or "?"
                if g.get("inning_half") == "초":  # top: away batting, home pitching
                    pitcher, batter = b_player, t_player
                else:                             # bottom: home batting, away pitching
                    pitcher, batter = t_player, b_player
                item.add(rumps.MenuItem(f"투수: {pitcher}  타자: {batter}"))

            elif status == STATUS_FINAL:
                if not detail:
                    item.add(rumps.MenuItem("로딩 중..."))
                else:
                    wp = g.get("win_pitcher")
                    lp = g.get("loss_pitcher")
                    sp = g.get("save_pitcher")
                    if wp or lp:
                        parts = []
                        if wp: parts.append(f"승 {wp}")
                        if lp: parts.append(f"패 {lp}")
                        if sp: parts.append(f"세 {sp}")
                        item.add(rumps.MenuItem("  ".join(parts)))

        return item

    # ------------------------------------------------------------------
    # Settings / favorite team
    # ------------------------------------------------------------------

    def _make_settings_item(self) -> rumps.MenuItem:
        settings = rumps.MenuItem("Settings")
        fav_menu = rumps.MenuItem("Favorite Team")

        none_item = rumps.MenuItem("None", callback=lambda _: self._set_favorite(None))
        none_item.state = 1 if not self._favorite else 0
        fav_menu.add(none_item)
        fav_menu.add(rumps.separator)

        for team in KBO_TEAMS:
            item = rumps.MenuItem(f"{TEAM_ICONS[team]} {team}", callback=self._make_fav_cb(team))
            item.state = 1 if team == self._favorite else 0
            fav_menu.add(item)

        settings.add(fav_menu)

        toggle = rumps.MenuItem("Team Icon", callback=lambda _: self._toggle_team_icon())
        toggle.state = 1 if self._team_icon else 0
        settings.add(toggle)

        return settings

    def _make_fav_cb(self, team: str):
        def callback(_sender):
            self._set_favorite(team)
        return callback

    def _toggle_team_icon(self) -> None:
        self._team_icon = not self._team_icon
        cfg = _load_config()
        cfg["team_icon"] = self._team_icon
        _save_config(cfg)
        self._rebuild_menu()

    def _set_favorite(self, team: str | None) -> None:
        self._favorite = team
        with self._lock:
            self._fav_detail = None
        cfg = _load_config()
        if team:
            cfg["favorite_team"] = team
        else:
            cfg.pop("favorite_team", None)
        _save_config(cfg)
        self._rebuild_menu()
        threading.Thread(target=self._do_fetch, daemon=True).start()



if __name__ == "__main__":
    KBOBarApp().run()
