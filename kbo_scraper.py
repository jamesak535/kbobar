#!/usr/bin/env python3
"""KBO game scraper — KBO schedule + Naver Sports live/final data."""

import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_KBO_HEADERS = {
    "User-Agent":   "Mozilla/5.0",
    "Referer":      "https://www.koreabaseball.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}
_NAV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://sports.naver.com/",
}

STATUS_PREGAME   = "1"
STATUS_LIVE      = "2"
STATUS_FINAL     = "3"
STATUS_CANCELLED = "4"
STATUS_SUSPENDED = "5"


def _s(val) -> str | None:
    if val is None:
        return None
    v = str(val).strip()
    return v if v else None


def _naver_status(code: str, cancel: bool, suspended: bool) -> str:
    if suspended:
        return STATUS_SUSPENDED
    if cancel or code in ("CANCEL", "POSTPONED"):
        return STATUS_CANCELLED
    return {
        "BEFORE":  STATUS_PREGAME,
        "STARTED": STATUS_LIVE,
        "PLAYING": STATUS_LIVE,
        "RESULT":  STATUS_FINAL,
    }.get(code, STATUS_PREGAME)


def _parse_inning(s: str) -> tuple:
    """'8회말' → (8, '말'). Returns (None, None) on failure."""
    m = re.match(r"(\d+)회(초|말)", s or "")
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def _inn_dict_to_list(d: dict) -> list[str]:
    """Naver inningScore dict {'1':'0','2':'1',...} → ordered list of strings."""
    if not d:
        return []
    int_keys = [int(k) for k in d if str(k).lstrip("-").isdigit()]
    if not int_keys:
        return []
    n = max(int_keys)
    return [str(d.get(str(i), "-")) for i in range(1, n + 1)]


def _inn_list_to_list(lst: list) -> list[str]:
    """Naver record inn list of ints → list of strings."""
    return [str(x) if x is not None else "-" for x in lst]


# ── KBO schedule ───────────────────────────────────────────────────────────────

def _fetch_kbo_schedule(date_str: str) -> list[dict]:
    r = requests.post(
        "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
        headers=_KBO_HEADERS,
        data={"leId": "1", "srId": "0,1,3,4,5,6,7,9", "date": date_str},
        timeout=10,
    )
    data = r.json()
    if data.get("code") != "100":
        return []

    out = []
    for g in data.get("game", []):
        gid = g.get("G_ID")
        if not gid:
            continue
        reason = _s(g.get("CANCEL_SC_NM"))
        if reason == "정상경기":
            reason = None
        out.append({
            "game_id":       gid,
            "away":          _s(g.get("AWAY_NM")),
            "home":          _s(g.get("HOME_NM")),
            "away_id":       gid[8:10],   # positions 8–9 of YYYYMMDD{AWAY}{HOME}0
            "home_id":       gid[10:12],  # positions 10–11
            "time":          _s(g.get("G_TM")),
            "venue":         _s(g.get("S_NM")),
            "away_starter":  _s(g.get("T_PIT_P_NM")),
            "home_starter":  _s(g.get("B_PIT_P_NM")),
            "away_rank":     g.get("T_RANK_NO"),
            "home_rank":     g.get("B_RANK_NO"),
            "cancel_reason": reason,
        })
    return out


# ── Naver relay (live game state) ──────────────────────────────────────────────

def _fetch_naver_relay(naver_id: str) -> dict | None:
    r = requests.get(
        f"https://api-gw.sports.naver.com/schedule/games/{naver_id}/relay",
        headers=_NAV_HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return None
    trd = r.json().get("result", {}).get("textRelayData", {})
    if not trd:
        return None

    cgs = trd.get("currentGameState", {})

    # Build pcode → (name, side) lookup from both lineups
    pcode_info: dict[str, tuple] = {}
    for side_key, side in (("awayLineup", "away"), ("homeLineup", "home")):
        for role in ("batter", "pitcher"):
            for p in trd.get(side_key, {}).get(role, []):
                pcode = p.get("pcode")
                name  = p.get("name")
                if pcode and name:
                    pcode_info[str(pcode)] = (name, side)

    pitcher_pcode = str(cgs.get("pitcher", ""))
    batter_pcode  = str(cgs.get("batter",  ""))
    pitcher_name, pitcher_side = pcode_info.get(pitcher_pcode, (None, None))
    batter_name,  _            = pcode_info.get(batter_pcode,  (None, None))

    # Fallback: parse batter name from textRelays[0].title ("4번타자 에레디아")
    if not batter_name:
        relays = trd.get("textRelays", [])
        if relays:
            m = re.search(r"(?:\d+번)?타자\s+(\S+)", relays[0].get("title", ""))
            if m:
                batter_name = m.group(1)

    # Assign to away/home based on which lineup the pitcher belongs to
    if pitcher_side == "away":
        away_player, home_player = pitcher_name, batter_name
    elif pitcher_side == "home":
        away_player, home_player = batter_name, pitcher_name
    else:  # pitcher pcode not found in either lineup — orientation unknown
        away_player, home_player = None, None

    inn_score = trd.get("inningScore", {})
    raw_inn = trd.get("inn")
    if isinstance(raw_inn, str):
        inning, inning_half = _parse_inning(raw_inn)
    else:
        inning = int(raw_inn) if raw_inn is not None else None
        # Derive half from pitcher's side (most reliable); homeOrAway fallback.
        # homeOrAway: "0" = away batting (top/초), "1" = home batting (bottom/말).
        if pitcher_side == "home":
            inning_half = "초"
        elif pitcher_side == "away":
            inning_half = "말"
        else:
            ha = trd.get("homeOrAway")
            if ha == "0":
                inning_half = "초"
            elif ha == "1":
                inning_half = "말"
            else:
                inning_half = None
    lvm = trd.get("lastValidMetricOption", {})

    return {
        "inning":       inning,
        "inning_half":  inning_half,
        "away_score":   _s(cgs.get("awayScore")),
        "home_score":   _s(cgs.get("homeScore")),
        "away_hits":    _s(cgs.get("awayHit")),
        "home_hits":    _s(cgs.get("homeHit")),
        "away_errors":  _s(cgs.get("awayError")),
        "home_errors":  _s(cgs.get("homeError")),
        "away_bb":      _s(cgs.get("awayBallFour")),
        "home_bb":      _s(cgs.get("homeBallFour")),
        "balls":        int(cgs.get("ball")   or 0),
        "strikes":      int(cgs.get("strike") or 0),
        "outs":         int(cgs.get("out")    or 0),
        "base1":        cgs.get("base1", "0") != "0",
        "base2":        cgs.get("base2", "0") != "0",
        "base3":        cgs.get("base3", "0") != "0",
        "away_innings": _inn_dict_to_list(inn_score.get("away", {})),
        "home_innings": _inn_dict_to_list(inn_score.get("home", {})),
        "away_player":  away_player,
        "home_player":  home_player,
        "away_win_pct": lvm.get("awayTeamWinRate"),
        "home_win_pct": lvm.get("homeTeamWinRate"),
    }


# ── Naver record (final game detail) ──────────────────────────────────────────

def _fetch_naver_record(naver_id: str) -> dict | None:
    r = requests.get(
        f"https://api-gw.sports.naver.com/schedule/games/{naver_id}/record",
        headers=_NAV_HEADERS,
        timeout=10,
    )
    if r.status_code != 200:
        return None
    rd = r.json().get("result", {}).get("recordData", {})
    if not rd:
        return None

    sb        = rd.get("scoreBoard", {})
    ra        = sb.get("rheb", {}).get("away", {})
    rh        = sb.get("rheb", {}).get("home", {})
    inn       = sb.get("inn", {})

    win_pitcher = loss_pitcher = save_pitcher = None
    for p in rd.get("pitchingResult", []):
        name, wls = _s(p.get("name")), p.get("wls")
        if   wls == "W": win_pitcher  = name
        elif wls == "L": loss_pitcher = name
        elif wls == "S": save_pitcher = name

    return {
        "away_score":   str(ra.get("r", "-")),
        "home_score":   str(rh.get("r", "-")),
        "away_hits":    str(ra.get("h", "-")),
        "home_hits":    str(rh.get("h", "-")),
        "away_errors":  str(ra.get("e", "-")),
        "home_errors":  str(rh.get("e", "-")),
        "away_bb":      str(ra.get("b", "-")),
        "home_bb":      str(rh.get("b", "-")),
        "away_innings": _inn_list_to_list(inn.get("away", [])),
        "home_innings": _inn_list_to_list(inn.get("home", [])),
        "win_pitcher":  win_pitcher,
        "loss_pitcher": loss_pitcher,
        "save_pitcher": save_pitcher,
    }


# ── Orientation helpers ────────────────────────────────────────────────────────

_SWAP_PAIRS = [
    ("away_score",   "home_score"),
    ("away_hits",    "home_hits"),
    ("away_errors",  "home_errors"),
    ("away_bb",      "home_bb"),
    ("away_innings", "home_innings"),
    ("away_player",  "home_player"),
    ("away_win_pct", "home_win_pct"),
]


def _swap_orientation(d: dict) -> dict:
    """Swap all away/home fields when Naver's team order disagrees with KBO's."""
    out = dict(d)
    for a, h in _SWAP_PAIRS:
        out[a], out[h] = d.get(h), d.get(a)
    # inning_half is orientation-dependent: top (초) ↔ bottom (말)
    half = d.get("inning_half")
    if half == "초":
        out["inning_half"] = "말"
    elif half == "말":
        out["inning_half"] = "초"
    return out


# ── Public API ─────────────────────────────────────────────────────────────────

def get_today_games(date_str: str | None = None) -> list[dict]:
    if date_str is None:
        date_str = datetime.now(KST).strftime("%Y%m%d")
    year       = int(date_str[:4])
    naver_date = f"{year}-{date_str[4:6]}-{date_str[6:8]}"

    # Step 1 — KBO schedule (team names, starters, venue, ranks)
    kbo_games = _fetch_kbo_schedule(date_str)
    if not kbo_games:
        return []

    # Step 2 — Naver schedule (game statuses + basic scores, one call)
    naver_by_id: dict[str, dict] = {}
    try:
        r = requests.get(
            "https://api-gw.sports.naver.com/schedule/games",
            params={"date": naver_date, "upperCategoryId": "kbaseball"},
            headers=_NAV_HEADERS,
            timeout=10,
        )
        for ng in r.json().get("result", {}).get("games", []):
            nid = ng.get("gameId", "")
            # Strip year suffix (e.g. "20260520SKWO02026" → "20260520SKWO0")
            kbo_id = nid[:-4] if len(nid) > 13 and nid[-4:].isdigit() else nid
            ng["_naver_id"] = nid  # preserve original so detail fetch doesn't need to reconstruct
            naver_by_id[kbo_id] = ng
    except Exception:
        pass  # fall back to KBO-only data; all games will show STATUS_PREGAME

    # Step 3 — Merge and enrich per game
    games = []
    for kbo in kbo_games:
        gid   = kbo["game_id"]
        naver = naver_by_id.get(gid, {})
        nid   = naver.get("_naver_id") or f"{gid}{year}"

        n_code    = naver.get("statusCode", "BEFORE")
        cancel    = bool(naver.get("cancel"))
        suspended = bool(naver.get("suspended"))
        status    = _naver_status(n_code, cancel, suspended)

        inning, inning_half = _parse_inning(naver.get("statusInfo") or "")

        # Correct score orientation: verify Naver's awayTeamCode matches KBO.
        # needs_swap is also applied to live/final detail below so both sources
        # use the same corrected orientation.
        naver_away = naver.get("awayTeamCode", "")
        needs_swap = bool(naver_away and naver_away != kbo["away_id"])
        if needs_swap:
            away_score = _s(naver.get("homeTeamScore")) or "0"
            home_score = _s(naver.get("awayTeamScore")) or "0"
        else:
            away_score = _s(naver.get("awayTeamScore")) or "0"
            home_score = _s(naver.get("homeTeamScore")) or "0"

        game: dict = {
            "game_id":       gid,
            "away":          kbo["away"],
            "home":          kbo["home"],
            "away_id":       kbo["away_id"],
            "home_id":       kbo["home_id"],
            "away_score":    away_score,
            "home_score":    home_score,
            "status":        status,
            "inning":        inning,
            "inning_half":   inning_half,
            "balls":         None,
            "strikes":       None,
            "outs":          None,
            "venue":         kbo["venue"],
            "time":          kbo["time"],
            "away_starter":  kbo["away_starter"],
            "home_starter":  kbo["home_starter"],
            "away_player":   None,
            "home_player":   None,
            "win_pitcher":   None,
            "loss_pitcher":  None,
            "save_pitcher":  None,
            "away_rank":     kbo["away_rank"],
            "home_rank":     kbo["home_rank"],
            "base1":         None,
            "base2":         None,
            "base3":         None,
            "away_innings":  None,
            "home_innings":  None,
            "away_hits":     None,
            "home_hits":     None,
            "away_errors":   None,
            "home_errors":   None,
            "away_bb":       None,
            "home_bb":       None,
            "away_win_pct":  None,
            "home_win_pct":  None,
            "cancel_reason": kbo["cancel_reason"],
        }

        try:
            if status == STATUS_LIVE:
                detail = _fetch_naver_relay(nid)
                if detail:
                    if needs_swap:
                        detail = _swap_orientation(detail)
                    game.update(detail)
            elif status == STATUS_FINAL:
                detail = _fetch_naver_record(nid)
                if detail:
                    if needs_swap:
                        detail = _swap_orientation(detail)
                    game.update(detail)
        except Exception:
            pass

        games.append(game)

    return games


def get_game_detail(_game_id: str) -> None:
    """Removed — detail is now included in get_today_games()."""
    return None


if __name__ == "__main__":
    games = get_today_games()
    for g in games:
        inn   = f" {g['inning']}회{g['inning_half'] or ''}" if g["inning"] else ""
        bases = ""
        if g.get("base1") is not None:
            bases = (
                " ["
                + ("◆" if g["base3"] else "◇")
                + ("◆" if g["base2"] else "◇")
                + ("◆" if g["base1"] else "◇")
                + "]"
            )
        print(
            f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']}"
            f"  [{g['status']}{inn}]{bases}  {g['venue']} {g['time']}  id={g['game_id']}"
        )
        if g.get("away_innings"):
            print(f"  linescore : {g['away_innings']} / {g['home_innings']}")
            print(
                f"  RHEB away : R={g['away_score']} H={g['away_hits']}"
                f" E={g['away_errors']} B={g['away_bb']}"
            )
            print(
                f"  RHEB home : R={g['home_score']} H={g['home_hits']}"
                f" E={g['home_errors']} B={g['home_bb']}"
            )
        if g.get("win_pitcher"):
            print(f"  pitchers  : W={g['win_pitcher']} L={g['loss_pitcher']} S={g.get('save_pitcher')}")
