#!/usr/bin/env python3
"""Own your Oura data: pull it into a local SQLite file and view it in a local dashboard.

Standard library only. Everything stays on your machine (default: ~/.oura-dashboard).

Commands:
  python oura.py auth        # one-time OAuth2 login (needs an Oura API application)
  python oura.py sync        # fetch recent data from the Oura API into SQLite
  python oura.py import PATH # load an Oura data export (JSON/CSV file or folder) into SQLite
  python oura.py dashboard   # write dashboard.html from the local database
  python oura.py daily       # sync + dashboard (run this from cron)
  python oura.py demo        # fill a separate demo DB with synthetic data and build its dashboard
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import http.server
import json
import os
import random
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HOME = Path(os.environ.get("OURA_HOME", Path.home() / ".oura-dashboard"))
CONFIG_FILE = HOME / "config.json"
TOKEN_FILE = HOME / "tokens.json"
DB_FILE = HOME / "oura.db"
HERE = Path(__file__).resolve().parent

API_BASE = "https://api.ouraring.com/v2/usercollection"
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
DEFAULT_TOKEN_URL = "https://api.ouraring.com/oauth/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "personal daily heartrate workout tag session spo2"

# Collections keyed by day (start_date/end_date params).
DAILY_COLLECTIONS = [
    "daily_sleep", "daily_readiness", "daily_activity", "sleep",
    "daily_spo2", "daily_stress", "daily_resilience", "workout", "session",
]
# Heart rate is a time series (start_datetime/end_datetime params) and is large; opt-in.
HEARTRATE = "heartrate"


# ---------------------------------------------------------------- storage

def db(path: Path = DB_FILE) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS records (
               collection TEXT NOT NULL,
               id TEXT NOT NULL,
               day TEXT,
               data TEXT NOT NULL,
               source TEXT NOT NULL,
               fetched_at TEXT NOT NULL,
               PRIMARY KEY (collection, id))"""
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_day ON records(collection, day)")
    return con


def upsert(con: sqlite3.Connection, collection: str, rows: list[dict], source: str) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    n = 0
    for r in rows:
        day = r.get("day") or str(r.get("timestamp", ""))[:10] or None
        rid = str(r.get("id") or f"{day}:{r.get('timestamp', '')}")
        con.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)",
            (collection, rid, day, json.dumps(r, separators=(",", ":")), source, now),
        )
        n += 1
    con.commit()
    return n


def write_private(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- auth

def load_config() -> dict:
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    cfg.setdefault("client_id", os.environ.get("OURA_CLIENT_ID"))
    cfg.setdefault("client_secret", os.environ.get("OURA_CLIENT_SECRET"))
    cfg.setdefault("token_url", os.environ.get("OURA_TOKEN_URL", DEFAULT_TOKEN_URL))
    return cfg


def post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Token request failed ({e.code}): {e.read().decode(errors='replace')}")


def save_tokens(tok: dict) -> dict:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 0)) - 60
    write_private(TOKEN_FILE, tok)
    return tok


def cmd_auth(_args) -> None:
    cfg = load_config()
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        cfg["client_id"] = input("Oura client_id: ").strip()
        cfg["client_secret"] = input("Oura client_secret: ").strip()
        write_private(CONFIG_FILE, cfg)

    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg["client_id"],
        "redirect_uri": REDIRECT_URI, "scope": SCOPES, "state": state,
    })
    print(f"Opening browser to authorize. If it doesn't open, visit:\n  {url}")
    webbrowser.open(url)

    deadline = time.time() + 300
    while "code" not in result and "error" not in result and time.time() < deadline:
        time.sleep(0.5)
    server.server_close()
    if result.get("state") != state or "code" not in result:
        sys.exit(f"Authorization failed: {result or 'timed out'}")

    tok = post_form(cfg["token_url"], {
        "grant_type": "authorization_code", "code": result["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
    })
    save_tokens(tok)
    print(f"Authorized. Tokens saved to {TOKEN_FILE} (mode 600).")


def access_token() -> str:
    # A personal access token still works for anyone who has one; OAuth is the supported path.
    if os.environ.get("OURA_PAT"):
        return os.environ["OURA_PAT"]
    if not TOKEN_FILE.exists():
        sys.exit("Not authorized. Run: python oura.py auth   (or set OURA_PAT)")
    tok = json.loads(TOKEN_FILE.read_text())
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    cfg = load_config()
    # Oura refresh tokens are single-use: save the new pair immediately or you'll be locked out.
    new = post_form(cfg["token_url"], {
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
    })
    new.setdefault("refresh_token", tok["refresh_token"])
    return save_tokens(new)["access_token"]


# ---------------------------------------------------------------- API sync

def api_get(collection: str, params: dict, token: str) -> list[dict]:
    rows, next_token = [], None
    while True:
        p = dict(params, **({"next_token": next_token} if next_token else {}))
        req = urllib.request.Request(
            f"{API_BASE}/{collection}?{urllib.parse.urlencode(p)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if e.code in (401, 403, 404):
                    print(f"  {collection}: skipped (HTTP {e.code} - missing scope or not on your plan)")
                    return rows
                raise
        rows.extend(body.get("data", []))
        next_token = body.get("next_token")
        if not next_token:
            return rows


def cmd_sync(args) -> None:
    token = access_token()
    end = dt.date.today() + dt.timedelta(days=1)  # end_date is exclusive for some collections
    start = dt.date.today() - dt.timedelta(days=args.days)
    con = db()
    print(f"Syncing {start} -> {end - dt.timedelta(days=1)}")
    for c in DAILY_COLLECTIONS:
        rows = api_get(c, {"start_date": start.isoformat(), "end_date": end.isoformat()}, token)
        print(f"  {c}: {upsert(con, c, rows, 'api')} records")
    if args.heartrate:
        # The heartrate endpoint caps each request at a 30-day window.
        s = dt.datetime.combine(start, dt.time())
        e_all = dt.datetime.combine(end, dt.time())
        total = 0
        while s < e_all:
            e = min(s + dt.timedelta(days=30), e_all)
            rows = api_get(HEARTRATE, {"start_datetime": s.isoformat() + "Z",
                                       "end_datetime": e.isoformat() + "Z"}, token)
            total += upsert(con, HEARTRATE, rows, "api")
            s = e
        print(f"  {HEARTRATE}: {total} samples")
    latest = con.execute("SELECT MAX(day) FROM records WHERE collection='daily_sleep'").fetchone()[0]
    print(f"Latest sleep day in database: {latest}")


# ---------------------------------------------------------------- export import

def _coerce(v: str):
    if v is None or v == "":
        return None
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    if v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            pass
    return v


def _guess_collection(name: str) -> str | None:
    n = name.lower().replace("-", "_").replace(" ", "_")
    for c in DAILY_COLLECTIONS + [HEARTRATE]:
        if c in n:
            return c
    # Older exports used v1 names.
    legacy = {"readiness": "daily_readiness", "activity": "daily_activity", "sleep": "sleep"}
    for k, c in legacy.items():
        if k in n:
            return c
    return None


def cmd_import(args) -> None:
    root = Path(args.path).expanduser()
    files = sorted(root.rglob("*")) if root.is_dir() else [root]
    con = db()
    for f in files:
        if f.suffix.lower() not in (".json", ".csv"):
            continue
        if f.suffix.lower() == ".csv":
            c = _guess_collection(f.stem)
            if not c:
                print(f"  {f.name}: skipped (unknown collection name)")
                continue
            with f.open(newline="", encoding="utf-8-sig") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                rows = [{k: _coerce(v) for k, v in r.items()} for r in csv.DictReader(fh, dialect=dialect)]
            print(f"  {f.name} -> {c}: {upsert(con, c, rows, 'export')} records")
            continue
        body = json.loads(f.read_text(encoding="utf-8-sig"))
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            groups = {_guess_collection(f.stem): body["data"]}
        elif isinstance(body, dict):
            groups = {_guess_collection(k): v for k, v in body.items() if isinstance(v, list)}
        elif isinstance(body, list):
            groups = {_guess_collection(f.stem): body}
        else:
            groups = {}
        for c, rows in groups.items():
            if c and rows and isinstance(rows[0], dict):
                print(f"  {f.name} -> {c}: {upsert(con, c, rows, 'export')} records")
            else:
                print(f"  {f.name}: a section was skipped (unrecognized format)")


# ---------------------------------------------------------------- dashboard

def _g(d: dict, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def build_series(con: sqlite3.Connection) -> list[dict]:
    days: dict[str, dict] = {}

    def row(day):
        return days.setdefault(day, {"day": day})

    def each(collection):
        for day, data in con.execute(
            "SELECT day, data FROM records WHERE collection=? AND day IS NOT NULL ORDER BY day", (collection,)
        ):
            yield day, json.loads(data)

    for day, d in each("daily_sleep"):
        row(day)["sleep_score"] = d.get("score")
    for day, d in each("daily_readiness"):
        r = row(day)
        r["readiness_score"] = d.get("score")
        r["temp_dev"] = d.get("temperature_deviation")
    for day, d in each("daily_activity"):
        r = row(day)
        r["activity_score"] = d.get("score")
        r["steps"] = d.get("steps")
        r["active_cal"] = d.get("active_calories")
    for day, d in each("daily_spo2"):
        row(day)["spo2"] = _g(d, "spo2_percentage", "average")
    for day, d in each("daily_stress"):
        r = row(day)
        r["stress_min"] = round((d.get("stress_high") or 0) / 60) if d.get("stress_high") is not None else None
        r["recovery_min"] = round((d.get("recovery_high") or 0) / 60) if d.get("recovery_high") is not None else None
    # Main sleep period per day (longest session, preferring type "long_sleep").
    best: dict[str, dict] = {}
    for day, d in each("sleep"):
        cur = best.get(day)
        key = (d.get("type") == "long_sleep", d.get("total_sleep_duration") or 0)
        if not cur or key > (cur.get("type") == "long_sleep", cur.get("total_sleep_duration") or 0):
            best[day] = d
    for day, d in best.items():
        r = row(day)
        hrs = lambda s: round(s / 3600, 2) if isinstance(s, (int, float)) else None
        r.update({
            "total_sleep_h": hrs(d.get("total_sleep_duration")),
            "deep_h": hrs(d.get("deep_sleep_duration")),
            "rem_h": hrs(d.get("rem_sleep_duration")),
            "light_h": hrs(d.get("light_sleep_duration")),
            "efficiency": d.get("efficiency"),
            "hrv": d.get("average_hrv"),
            "lowest_hr": d.get("lowest_heart_rate"),
            "bedtime_start": d.get("bedtime_start"),
        })
    return [days[k] for k in sorted(days)]


def cmd_dashboard(args, db_path: Path = DB_FILE, out: Path | None = None) -> Path:
    con = db(db_path)
    series = build_series(con)
    counts = dict(con.execute("SELECT collection, COUNT(*) FROM records GROUP BY collection").fetchall())
    template = (HERE / "dashboard_template.html").read_text()
    payload = json.dumps({
        "series": series, "counts": counts,
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
    }, separators=(",", ":"))
    # Inline Chart.js so the page works offline and loads nothing from third parties.
    vendored = HERE / "vendor" / "chart.umd.min.js"
    if vendored.exists():
        cdn_tag = next(l for l in template.splitlines() if "chart.umd.min.js" in l)
        js = vendored.read_text().replace("</script", "<\\/script")
        template = template.replace(cdn_tag, f"<script>{js}</script>")
    out = out or Path(getattr(args, "out", None) or HOME / "dashboard.html")
    out.write_text(template.replace("/*__DATA__*/null", payload))
    print(f"Dashboard written: {out}  ({len(series)} days)")
    return out


def cmd_daily(args) -> None:
    cmd_sync(args)
    cmd_dashboard(args)


# ---------------------------------------------------------------- demo data

def cmd_demo(args) -> None:
    path = HOME / "demo.db"
    if path.exists():
        path.unlink()
    con = db(path)
    rnd = random.Random(7)
    today = dt.date.today()
    hrv = 45.0
    for i in range(180, 0, -1):
        day = (today - dt.timedelta(days=i)).isoformat()
        hrv = max(20, min(80, hrv + rnd.gauss(0, 3)))
        total = rnd.gauss(7.1, 0.7) * 3600
        deep, rem = total * rnd.uniform(.13, .22), total * rnd.uniform(.18, .26)
        upsert(con, "sleep", [{
            "id": f"s{i}", "day": day, "type": "long_sleep",
            "total_sleep_duration": int(total), "deep_sleep_duration": int(deep),
            "rem_sleep_duration": int(rem), "light_sleep_duration": int(total - deep - rem),
            "efficiency": rnd.randint(80, 96), "average_hrv": round(hrv),
            "lowest_heart_rate": round(62 - hrv / 6 + rnd.gauss(0, 1.5)),
            "bedtime_start": f"{day}T23:{rnd.randint(0, 59):02d}:00",
        }], "demo")
        sleep_score = int(max(50, min(98, 60 + (total / 3600 - 6) * 12 + rnd.gauss(0, 4))))
        upsert(con, "daily_sleep", [{"id": f"ds{i}", "day": day, "score": sleep_score}], "demo")
        upsert(con, "daily_readiness", [{
            "id": f"dr{i}", "day": day,
            "score": int(max(45, min(98, 50 + hrv / 2 + rnd.gauss(0, 5)))),
            "temperature_deviation": round(rnd.gauss(0, 0.25), 2),
        }], "demo")
        steps = max(1500, int(rnd.gauss(8500, 2800)))
        upsert(con, "daily_activity", [{
            "id": f"da{i}", "day": day, "score": int(max(40, min(99, 55 + steps / 400 + rnd.gauss(0, 4)))),
            "steps": steps, "active_calories": int(steps * 0.045),
        }], "demo")
        upsert(con, "daily_spo2", [{"id": f"o{i}", "day": day,
                                    "spo2_percentage": {"average": round(rnd.uniform(95.5, 98.5), 1)}}], "demo")
        upsert(con, "daily_stress", [{"id": f"st{i}", "day": day,
                                      "stress_high": rnd.randint(0, 180) * 60,
                                      "recovery_high": rnd.randint(30, 240) * 60}], "demo")
    cmd_dashboard(args, db_path=path, out=Path(args.out or HOME / "demo_dashboard.html"))


# ---------------------------------------------------------------- cli

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth").set_defaults(fn=cmd_auth)
    for name, fn in (("sync", cmd_sync), ("daily", cmd_daily)):
        p = sub.add_parser(name)
        p.add_argument("--days", type=int, default=14, help="days back to fetch (use ~3650 for full history)")
        p.add_argument("--heartrate", action="store_true", help="also fetch 5-minute heart rate samples")
        p.add_argument("--out", help="dashboard output path")
        p.set_defaults(fn=fn)
    p = sub.add_parser("import")
    p.add_argument("path")
    p.set_defaults(fn=cmd_import)
    for name, fn in (("dashboard", cmd_dashboard), ("demo", cmd_demo)):
        p = sub.add_parser(name)
        p.add_argument("--out")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
