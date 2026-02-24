#!/usr/bin/env python3
"""
IG-friendly "SCHEDULE PRESSURE" card for ALL NHL games on a given day
(using the NEW NHL API: https://api-web.nhle.com/v1)

Renders 1080x1080 PNG slides:
- Title + date
- List of games (Away @ Home + local start time)
- For each team: TRVL (km since last game venue), B2B, 3IN4, 4IN6
- Footer "FATIGUE WATCH" + faded logo inside footer

Usage:
  python nhl_schedule_pressure_card.py --date 2026-01-23
  python nhl_schedule_pressure_card.py --date 2026-01-23 --per 5 --outdir ig_pressure
  python nhl_schedule_pressure_card.py  # defaults to today (America/Toronto)

Outputs:
  ig_pressure/ig_schedule_pressure_YYYY-MM-DD_p1.png
  ig_pressure/ig_schedule_pressure_YYYY-MM-DD_p2.png ...
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import csv
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import base64
import json


API = "https://api-web.nhle.com/v1"
ET = ZoneInfo("America/Toronto")

LOGO_PATH = r"stat_trick_logo.png"     # your logo file path
ARENAS_CSV_PATH = "nhl_arenas.csv"     # team_abbr + lat/lon (recommended full header)


# ============================================================
# GitHub Publish (Contents API)
# ============================================================
GITHUB_PUBLISH = True  # toggle

# Read token from environment variable (set as GitHub Actions secret GH_TOKEN)
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER  = "stat-trick-hockey"
GITHUB_REPO   = "ig_pressure"
GITHUB_BRANCH = "main"
GITHUB_PAGES_DIR = "docs"
GITHUB_SUBDIR = "ig_pressure"

GITHUB_USER_AGENT = os.getenv("GITHUB_USER_AGENT", "Mozilla/5.0 (compatible; GitHubPublisher/1.0)")


# ============================================================
# NHL API helpers
# ============================================================

def _gh_headers() -> dict:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set. Set env var GITHUB_TOKEN to enable GitHub publish.")
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": GITHUB_USER_AGENT,
    }


def _gh_get_file_sha(owner: str, repo: str, path: str, branch: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=_gh_headers(), timeout=20)
    if r.status_code == 200:
        js = r.json()
        return js.get("sha")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return None


def gh_put_file(owner: str, repo: str, path: str, content_bytes: bytes, message: str, branch: str) -> dict:
    sha = _gh_get_file_sha(owner, repo, path, branch)
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=_gh_headers(), data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    return r.json()


def publish_images_to_github(image_paths: list[str], date_str: str) -> list[str]:
    if not (GITHUB_OWNER and GITHUB_REPO and GITHUB_BRANCH):
        raise RuntimeError("Set GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH to publish.")

    uploaded_urls: list[str] = []
    for p in image_paths:
        fname = os.path.basename(p)
        repo_path = f"{GITHUB_PAGES_DIR}/{GITHUB_SUBDIR}/{fname}".replace("\\", "/")

        with open(p, "rb") as f:
            b = f.read()

        msg = f"Publish schedule pressure images ({date_str}): {fname}"
        resp = gh_put_file(GITHUB_OWNER, GITHUB_REPO, repo_path, b, msg, GITHUB_BRANCH)

        pages_url = f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/{repo_path.replace(GITHUB_PAGES_DIR+'/', '')}"
        download_url = (resp.get("content") or {}).get("download_url")

        uploaded_urls.append(pages_url if pages_url else (download_url or ""))

    return uploaded_urls


def _get_json(url: str, timeout: int = 20) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_schedule(date_yyyy_mm_dd: str) -> list[dict]:
    js = _get_json(f"{API}/schedule/{date_yyyy_mm_dd}")

    games: list[dict] = []
    if isinstance(js, dict):
        if "gameWeek" in js and isinstance(js["gameWeek"], list):
            for day in js["gameWeek"]:
                if str(day.get("date", "")) == date_yyyy_mm_dd:
                    games.extend(day.get("games", []) or [])
        if not games and "games" in js and isinstance(js["games"], list):
            games = js["games"]

    return games


def fetch_schedules_for_range(start_date: dt.date, end_date: dt.date) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    cur = start_date
    while cur <= end_date:
        ds = cur.isoformat()
        try:
            out[ds] = fetch_schedule(ds)
        except Exception:
            out[ds] = []
        cur += dt.timedelta(days=1)
    return out


# ============================================================
# Arenas + travel distance
# ============================================================

def load_arenas_latlon(path: str) -> dict[str, tuple[float, float]]:
    m: dict[str, tuple[float, float]] = {}

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return m

    header = [c.strip() for c in rows[0]]
    has_headers = any(h.lower() in ("team_abbr", "lat", "lon") for h in header)

    if has_headers:
        col_idx = {name.lower(): i for i, name in enumerate(header)}
        team_i = col_idx.get("team_abbr")
        lat_i = col_idx.get("lat")
        lon_i = col_idx.get("lon")
        if team_i is None or lat_i is None or lon_i is None:
            raise ValueError("Arenas CSV must include team_abbr, lat, lon columns.")
        data_rows = rows[1:]
        for r in data_rows:
            if len(r) <= max(team_i, lat_i, lon_i):
                continue
            ab = str(r[team_i]).strip().upper()
            try:
                m[ab] = (float(r[lat_i]), float(r[lon_i]))
            except Exception:
                continue
    else:
        for r in rows:
            if len(r) < 6:
                continue
            ab = str(r[0]).strip().upper()
            try:
                m[ab] = (float(r[4]), float(r[5]))
            except Exception:
                continue

    return m


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float("nan")
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ============================================================
# Fatigue metrics
# ============================================================

@dataclass
class TeamLoad:
    team: str
    b2b: bool
    g3in4: int
    g4in6: int
    travel_km: float | None


def _team_abbrev(team_obj: dict) -> str:
    a = team_obj.get("abbrev")
    if a:
        return str(a).upper()
    for k in ("triCode", "shortName", "teamAbbrev"):
        if team_obj.get(k):
            return str(team_obj[k]).upper()
    return "UNK"


def _fmt_local_time(game: dict, tz: ZoneInfo = ET) -> str:
    s = game.get("startTimeUTC") or game.get("startTime") or ""
    if not s:
        return ""
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt_utc = dt.datetime.fromisoformat(s)
        dt_loc = dt_utc.astimezone(tz)
        hour_fmt = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"
        return dt_loc.strftime(hour_fmt)
    except Exception:
        return ""


def compute_team_loads(
    day_games: list[dict],
    schedules_by_date: dict[str, list[dict]],
    target_date: dt.date,
    arenas_latlon: dict[str, tuple[float, float]] | None = None,
) -> dict[str, TeamLoad]:
    teams_today: set[str] = set()
    today_game_by_team: dict[str, dict] = {}

    for g in day_games:
        away = _team_abbrev(g.get("awayTeam") or {})
        home = _team_abbrev(g.get("homeTeam") or {})
        teams_today.update([away, home])
        today_game_by_team.setdefault(away, g)
        today_game_by_team.setdefault(home, g)

    def game_teams(gg: dict) -> tuple[str, str]:
        a = _team_abbrev(gg.get("awayTeam") or {})
        h = _team_abbrev(gg.get("homeTeam") or {})
        return a, h

    def count_games_for_team(team: str, start: dt.date, end: dt.date) -> int:
        n = 0
        cur = start
        while cur <= end:
            ds = cur.isoformat()
            for gg in schedules_by_date.get(ds, []):
                a, h = game_teams(gg)
                if team in (a, h):
                    n += 1
            cur += dt.timedelta(days=1)
        return n

    def venue_latlon_for_game(gg: dict) -> tuple[float, float] | None:
        if not arenas_latlon:
            return None
        _, home = game_teams(gg)
        return arenas_latlon.get(home)

    def travel_km_last_7_days(team: str) -> float | None:
        if not arenas_latlon:
            return None

        start_day = target_date - dt.timedelta(days=6)

        games_window: list[tuple[dt.date, dict]] = []
        for ds in sorted(schedules_by_date.keys()):
            dcur = dt.date.fromisoformat(ds)
            if start_day <= dcur <= target_date:
                for gg in schedules_by_date.get(ds, []):
                    a, h = game_teams(gg)
                    if team in (a, h):
                        games_window.append((dcur, gg))

        if len(games_window) < 2:
            return None

        games_window.sort(key=lambda x: x[0])
        venues: list[tuple[float, float]] = []
        for _, gg in games_window:
            ll = venue_latlon_for_game(gg)
            if ll:
                venues.append(ll)

        if len(venues) < 2:
            return None

        km_sum = 0.0
        for (lat1, lon1), (lat2, lon2) in zip(venues[:-1], venues[1:]):
            km = haversine_km(lat1, lon1, lat2, lon2)
            if not math.isnan(km):
                km_sum += km

        return float(km_sum)

    loads: dict[str, TeamLoad] = {}
    d = target_date
    d_yday = d - dt.timedelta(days=1)

    w3_start = d - dt.timedelta(days=3)
    w6_start = d - dt.timedelta(days=5)

    for t in sorted(teams_today):
        g_yday = count_games_for_team(t, d_yday, d_yday)
        g3 = count_games_for_team(t, w3_start, d)
        g6 = count_games_for_team(t, w6_start, d)
        tkm = travel_km_last_7_days(t)

        loads[t] = TeamLoad(
            team=t,
            b2b=(g_yday > 0),
            g3in4=g3,
            g4in6=g6,
            travel_km=tkm,
        )

    return loads


# ============================================================
# Rendering (PIL)
# ============================================================

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if os.name == "nt":
        candidates = [
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, r: int, fill=None, outline=None, width: int = 1):
    draw.rounded_rectangle(list(xy), radius=r, fill=fill, outline=outline, width=width)


def _chip(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: ImageFont.ImageFont,
          fill, outline, text_fill, pad_x=12, pad_y=6, r=14):
    tw = draw.textlength(text, font=font)
    th = font.size
    w = int(tw + pad_x * 2)
    h = int(th + pad_y * 2)
    _rounded_rect(draw, (x, y, x + w, y + h), r=r, fill=fill, outline=outline, width=2)
    draw.text((x + pad_x, y + pad_y - 1), text, font=font, fill=text_fill)
    return w, h


def _soft_shadow(base_rgba, rect, r=28, offset=(0, 10), shadow_color=(0, 0, 0, 140), blur=22):
    x0, y0, x1, y1 = rect
    shadow = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x0 + offset[0], y0 + offset[1], x1 + offset[0], y1 + offset[1]],
                         radius=r, fill=shadow_color)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(base_rgba, shadow)


def _circle_logo(logo_path, size=52, opacity=90):
    logo = Image.open(logo_path).convert("RGBA").resize((size, size))

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, size - 1, size - 1), fill=255)
    logo.putalpha(mask)

    alpha = logo.split()[-1].point(lambda p: int(p * (opacity / 255)))
    logo.putalpha(alpha)
    return logo


def _fmt_km_compact(km: float | None) -> str:
    if km is None:
        return "—"
    if km < 1000:
        return f"{int(round(km))}"
    return f"{km/1000.0:.1f}k"


def render_schedule_pressure_card(date_str: str, games: list[dict], loads: dict[str, TeamLoad], out_path: str):
    W, H = 1080, 1080

    BG     = (11, 15, 20)
    CARD   = (16, 24, 36)
    CARD2  = (18, 28, 42)
    BORDER = (35, 52, 72)
    TEAL   = (59, 214, 198)
    TEXT   = (234, 242, 255)
    MUTED  = (156, 170, 190)
    HOT    = (255, 107, 107)

    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = _load_font(54, bold=True)
    f_sub   = _load_font(26, bold=False)
    f_row   = _load_font(34, bold=True)
    f_small = _load_font(22, bold=False)
    f_chip  = _load_font(20, bold=True)

    header = (45, 40, W - 45, 245)
    img = _soft_shadow(img, header, r=34)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, header, r=34, fill=CARD, outline=BORDER, width=3)
    d.rectangle([header[0] + 22, header[1] + 24, header[2] - 22, header[1] + 40], fill=TEAL)

    d.text((header[0] + 28, header[1] + 70), "NHL Schedule Pressure", font=f_title, fill=TEXT)
    d.text((header[0] + 28, header[1] + 145),
           f"{date_str} • All Games • TRVL + Density flags (B2B / 3IN4 / 4IN6)",
           font=f_sub, fill=MUTED)

    main = (45, 275, W - 45, 910)
    img = _soft_shadow(img, main, r=34)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, main, r=34, fill=CARD, outline=BORDER, width=3)

    lx, ly = main[0] + 28, main[1] + 22
    _chip(d, lx, ly, "TRVL (7D) = km in last 7 days", f_chip, CARD2, BORDER, TEXT); lx += 420
    _chip(d, lx, ly, "B2B = played yesterday", f_chip, CARD2, BORDER, TEXT); lx = main[0] + 28; ly += 46
    _chip(d, lx, ly, "3IN4 / 4IN6 include today", f_chip, CARD2, BORDER, TEXT)

    if not games:
        d.text((main[0] + 28, main[1] + 140), "No games found for this date.", font=f_row, fill=TEXT)
    else:
        games_sorted = sorted(games, key=lambda g: g.get("startTimeUTC") or "")

        x_left = main[0] + 28
        x_right = main[2] - 28
        y = main[1] + 130
        row_h = 96
        sep = (40, 55, 75)

        for i, g in enumerate(games_sorted):
            away = _team_abbrev(g.get("awayTeam") or {})
            home = _team_abbrev(g.get("homeTeam") or {})
            t = _fmt_local_time(g, ET)

            if i > 0:
                d.line((main[0] + 24, y - 14, main[2] - 24, y - 14), fill=sep, width=2)

            d.text((x_left, y - 6), f"{away} @ {home}", font=f_row, fill=TEXT)
            if t:
                d.text((x_left, y + 36), f"Start: {t} ET", font=f_small, fill=MUTED)

            def draw_flags(label: str, team: str, yy: int):
                load = loads.get(team)
                b2b = load.b2b if load else False
                g3  = load.g3in4 if load else 0
                g6  = load.g4in6 if load else 0
                km  = load.travel_km if load else None

                km_txt = _fmt_km_compact(km)
                travel_hot = (km is not None and km >= 3000)

                chips = [
                    ("TRVL", km_txt, travel_hot),
                    ("4IN6", str(g6), g6 >= 4),
                    ("3IN4", str(g3), g3 >= 3),
                    ("B2B",  "Y" if b2b else "N", b2b),
                ]

                curx = x_right
                for kind, val, hot in chips:
                    txt = f"{kind}:{val}"
                    tw = d.textlength(txt, font=f_chip)
                    w = int(tw + 14 * 2)
                    curx -= w
                    _chip(
                        d, curx, yy, txt, f_chip,
                        fill=CARD2,
                        outline=(HOT if hot else BORDER),
                        text_fill=TEXT,
                        pad_x=14, pad_y=8, r=18
                    )
                    curx -= 10

                labw = d.textlength(label, font=f_small)
                d.text((curx - labw - 8, yy + 6), label, font=f_small, fill=MUTED)

            draw_flags("AWAY", away, y)
            draw_flags("HOME", home, y + 44)

            y += row_h

    footer = (45, 935, W - 45, 1035)
    img = _soft_shadow(img, footer, r=34)
    d = ImageDraw.Draw(img)
    _rounded_rect(d, footer, r=34, fill=CARD, outline=BORDER, width=3)
    d.rectangle([footer[0] + 22, footer[1] + 22, footer[2] - 22, footer[1] + 38], fill=TEAL)

    d.text((footer[0] + 28, footer[1] + 46), "Fatigue Watch", font=_load_font(28, bold=True), fill=TEXT)
    d.text((footer[0] + 28, footer[1] + 78),
           "• Slower legs late   • Transition gaps   • Late penalties",
           font=_load_font(22, bold=False), fill=MUTED)

    try:
        logo_size = 52
        logo = _circle_logo(LOGO_PATH, size=logo_size, opacity=90)
        lx = footer[2] - 28 - logo_size
        ly = footer[3] - 8 - logo_size
        img.alpha_composite(logo, (lx, ly))
    except Exception:
        pass

    img.convert("RGB").save(out_path)
    print(f"✅ Wrote: {out_path}")


def render_schedule_pressure_carousel(date_str: str, games: list[dict], loads: dict[str, TeamLoad],
                                      out_dir: str = "ig_pressure", max_games_per_slide: int = 5) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)

    games_sorted = sorted(games, key=lambda g: g.get("startTimeUTC") or "")
    paths: list[str] = []

    if not games_sorted:
        out_path = os.path.join(out_dir, f"ig_schedule_pressure_{date_str}_p1.png")
        render_schedule_pressure_card(date_str, [], loads, out_path)
        return [out_path]

    for i in range(0, len(games_sorted), max_games_per_slide):
        chunk = games_sorted[i:i + max_games_per_slide]
        slide_n = (i // max_games_per_slide) + 1
        out_path = os.path.join(out_dir, f"ig_schedule_pressure_{date_str}_p{slide_n}.png")
        render_schedule_pressure_card(date_str, chunk, loads, out_path)
        paths.append(out_path)

    return paths


# ============================================================
# Main / Jupyter helper
# ============================================================

def build_loads_for_date(target: dt.date, arenas_path: str, history_days: int = 14) -> tuple[list[dict], dict[str, TeamLoad]]:
    date_str = target.isoformat()

    games_today = fetch_schedule(date_str)

    start = target - dt.timedelta(days=history_days)
    schedules = fetch_schedules_for_range(start, target)

    arenas_latlon = None
    if arenas_path and os.path.exists(arenas_path):
        try:
            arenas_latlon = load_arenas_latlon(arenas_path)
        except Exception:
            arenas_latlon = None

    loads = compute_team_loads(games_today, schedules, target, arenas_latlon=arenas_latlon)
    return games_today, loads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default: today in America/Toronto)")
    ap.add_argument("--outdir", default="ig_pressure", help="Output directory for slides")
    ap.add_argument("--per", type=int, default=5, help="Max games per slide")
    ap.add_argument("--arenas", default=ARENAS_CSV_PATH, help="Arenas CSV path (team_abbr, lat, lon)")
    ap.add_argument("--history_days", type=int, default=14, help="How many days back to search schedules")
    args = ap.parse_args()

    if args.date:
        target = dt.date.fromisoformat(args.date)
    else:
        target = dt.datetime.now(ET).date()

    games_today, loads = build_loads_for_date(target, arenas_path=args.arenas, history_days=args.history_days)
    paths = render_schedule_pressure_carousel(target.isoformat(), games_today, loads, out_dir=args.outdir, max_games_per_slide=args.per)

    if GITHUB_PUBLISH:
        try:
            urls = publish_images_to_github(paths, target.isoformat())
            print("✅ Published to GitHub:")
            for u in urls:
                print(" -", u)
        except Exception as e:
            print("⚠️ GitHub publish failed:", e)

    print("✅ Slides:")
    for p in paths:
        print(" -", p)


if __name__ == "__main__":
    main()
