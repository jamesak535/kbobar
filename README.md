# KBOBar

A lightweight macOS menu bar app that displays live KBO baseball scores. Written in Python using [rumps](https://github.com/jaredks/rumps), it pulls schedule data from the official KBO website and live/final game detail from the Naver Sports API.

---

## Features

- **Live scores in the menu bar** — shows team names, score, and current inning with top/bottom indicator (▲/▼) for every in-progress game
- **Favorite team mode** — pin one KBO team; the menu bar title shows only that team's status and their game is pinned to the top of the dropdown as a flat, always-visible block
- **Team icon** — optionally replace the ⚾ base icon with the favorite team's emoji (🐯 🦖 🐻 etc.)
- **Multi-game cycling** — when no favorite is set, the menu bar title cycles through all live games every 5 seconds
- **Cancelled and suspended games** — shows the cancellation reason (e.g. 우천취소) directly in the menu bar title
- **Pre-game info** — expandable submenu with venue, start time, starting pitchers, and each team's standings rank
- **Live game detail** — inning-by-inning linescore grid (R/H/E/B), ball-strike-out count, base runner diagram (◆◇◇), and current pitcher/batter names
- **Final game detail** — full linescore and winning/losing/save pitcher
- **Smart polling** — refreshes every 10 seconds during live games, every 30 seconds near game time (within 30 minutes of first pitch or 30 minutes after expected last end), and every 10 minutes otherwise
- **Persistent settings** — saved to `~/.kbobar/config.json`


## Data Sources

| Data | Source |
|---|---|
| Schedule, team names, starters, venue, standings rank | `koreabaseball.com` JSON API |
| Game status, live relay (count, bases, pitcher/batter), final linescore | `api-gw.sports.naver.com` |


## Requirements

- macOS (menu bar apps require AppKit)
- Python 3.10 (the venv and `build.sh` are wired to 3.10; `zoneinfo` and union-type annotations require 3.9+)
- For building a standalone `.app`: miniforge/conda installed at `/opt/homebrew/Caskroom/miniforge` (provides the OpenSSL dylibs bundled by `build.sh`)


## Installation

### Manual Installation

Download `KBOBar.zip` from the [latest release](../../releases/latest) and move the unzipped app into your `Applications` folder.

### Run from source

```bash
git clone <repo-url>
cd kbo_stats

# Create and activate the virtual environment using Python 3.10
python3.10 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python kbo_bar.py
```

The app appears as ⚾ in the macOS menu bar.

### Build a standalone .app bundle

This packages everything into `dist/KBOBar.app` using py2app. It fixes `@rpath/libffi` references in bundled `.so` files and copies OpenSSL dylibs from miniforge.

```bash
source .venv/bin/activate
pip install py2app
chmod +x build.sh
./build.sh
```

On success the script prints:

```
Build complete: dist/KBOBar.app
   Run with: open "dist/KBOBar.app"
```

Drag `dist/KBOBar.app` to your Applications folder to install it permanently.


## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `rumps` | 0.4.0 | macOS menu bar framework |
| `requests` | >=2.32 | HTTP requests to KBO and Naver APIs |
| `pyobjc-core` | >=10.0 | Objective-C bridge (required by rumps) |
| `pyobjc-framework-Cocoa` | >=10.0 | Cocoa bindings (required by rumps) |

`py2app` is a build-time dependency only; install it separately before running `build.sh`.


## Settings

All settings are accessible from the **Settings** submenu in the dropdown:

| Setting | Default | Description |
|---|---|---|
| Favorite Team | None | Pin one of the 10 KBO teams; their game is shown in the title bar and at the top of the menu |
| Team Icon | Off | Replace ⚾ with the favorite team's emoji in the menu bar |

Available teams: NC, 두산, LG, KIA, SSG, 키움, 롯데, 한화, KT, 삼성

Settings are written to `~/.kbobar/config.json` immediately on change.


## Scraper Library

`kbo_scraper.py` can also be used as a standalone module. Its single public function is:

```python
from kbo_scraper import get_today_games

games = get_today_games()              # defaults to today in KST
games = get_today_games("20260520")    # pass YYYYMMDD string for a specific date
```

Each element in the returned list is a `dict` with the following keys:

| Key | Type | Description |
|---|---|---|
| `game_id` | `str` | KBO game ID (e.g. `20260520SKWO0`) |
| `away` / `home` | `str` | Team name |
| `away_id` / `home_id` | `str` | Two-character team code from the game ID |
| `away_score` / `home_score` | `str` | Current or final run total |
| `status` | `str` | `"1"` pre-game, `"2"` live, `"3"` final, `"4"` cancelled, `"5"` suspended |
| `inning` | `int \| None` | Current inning number (live only) |
| `inning_half` | `str \| None` | `"초"` (top) or `"말"` (bottom) (live only) |
| `balls` / `strikes` / `outs` | `int \| None` | Current count (live only) |
| `base1` / `base2` / `base3` | `bool \| None` | Runner on base (live only) |
| `away_innings` / `home_innings` | `list[str] \| None` | Per-inning run totals (live/final) |
| `away_hits` / `home_hits` | `str \| None` | Hit totals (live/final) |
| `away_errors` / `home_errors` | `str \| None` | Error totals (live/final) |
| `away_bb` / `home_bb` | `str \| None` | Walk totals (live/final) |
| `away_player` / `home_player` | `str \| None` | Current pitcher/batter names (live only) |
| `away_win_pct` / `home_win_pct` | `any \| None` | Win probability from Naver (live only) |
| `win_pitcher` / `loss_pitcher` / `save_pitcher` | `str \| None` | Decision pitchers (final only) |
| `venue` | `str \| None` | Stadium name |
| `time` | `str \| None` | Scheduled start time (HH:MM) |
| `away_starter` / `home_starter` | `str \| None` | Starting pitcher names |
| `away_rank` / `home_rank` | `any \| None` | Current standings rank |
| `cancel_reason` | `str \| None` | Cancellation reason if status is `"4"` |

Running the module directly prints a compact scoreboard to stdout:

```bash
python kbo_scraper.py
```

Example output for a live game:

```
KIA 3 @ LG 2  [2 8회초]  잠실 18:30  id=20260520KIALG0
  linescore : ['0', '0', '1', '0', '0', '2', '0', '0'] / ['1', '0', '0', '0', '1', '0', '0', '-']
  RHEB away : R=3 H=6 E=0 B=2
  RHEB home : R=2 H=5 E=1 B=3
```


## Project Structure

```
kbo_stats/
├── kbo_bar.py          # rumps menu bar app (entry point)
├── kbo_scraper.py      # scraper library (public API: get_today_games)
├── requirements.txt    # runtime dependencies
├── setup.py            # py2app configuration for .app bundle
└──  build.sh            # build script: runs py2app, fixes dylib paths, codesigns
```


## License

[GPL-3.0](LICENSE)
