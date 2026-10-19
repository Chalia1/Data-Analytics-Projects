"""Log today's YYZ fare observations and flag deals >= threshold below baseline.

Usage:
    python check_deals.py observations.json

observations.json is a list of:
    {"dest": "LIS", "window": "6wk", "depart": "2026-12-03", "return": "2026-12-10",
     "price_usd": 524.0, "airline": "Air Canada", "stops": 1}

The baseline for a route+window is the median of prior observed prices. Routes
with fewer than min_history_for_baseline prior observations are still calibrating
and never alert -- guessed baselines produced mostly false positives.
"""
import csv
import json
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
HISTORY = ROOT / "data" / "price_history.csv"
ALERTS = ROOT / "data" / "alerts.md"
FIELDS = ["date", "dest", "window", "depart", "return", "price_usd", "airline", "stops"]


def load_history():
    if not HISTORY.exists():
        return []
    with HISTORY.open() as f:
        return list(csv.DictReader(f))


def baseline(history, dest, window, min_n):
    prices = [float(r["price_usd"]) for r in history if r["dest"] == dest and r["window"] == window]
    if len(prices) < min_n:
        return None, len(prices)
    return statistics.median(prices), len(prices)


def main(obs_path):
    cfg = json.loads((ROOT / "watchlist.json").read_text())
    dests = {d["code"]: d for d in cfg["destinations"]}
    threshold = cfg["deal_threshold"]
    history = load_history()
    today = date.today().isoformat()
    observations = json.loads(Path(obs_path).read_text())

    deals, calibrating = [], 0
    for o in observations:
        dest = dests.get(o["dest"])
        if dest is None:
            continue
        base, n = baseline(history, o["dest"], o["window"], cfg["min_history_for_baseline"])
        if base is None:
            calibrating += 1
            continue
        drop = 1 - o["price_usd"] / base
        if drop >= threshold:
            deals.append((drop, o, dest, base, f"median of {n} obs"))

    # Append after computing baselines so today's prices don't dilute their own comparison.
    new_file = not HISTORY.exists()
    with HISTORY.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for o in observations:
            w.writerow({"date": today, **{k: o.get(k, "") for k in FIELDS[1:]}})

    deals.sort(key=lambda d: -d[0])
    lines = [f"## {today}"]
    if calibrating:
        lines.append(f"{calibrating}/{len(observations)} route-windows still calibrating (need {cfg['min_history_for_baseline']} days of history).")
    if not deals:
        lines.append(f"No fares {threshold:.0%}+ below baseline.")
    for drop, o, dest, base, basis in deals:
        lines.append(
            f"- **YYZ→{o['dest']} ({dest['city']})** ${o['price_usd']:.0f} USD, {drop:.0%} below "
            f"${base:.0f} ({basis}) · {o['depart']}→{o['return']} · {o.get('airline', '?')}, {o.get('stops', '?')} stop(s)"
        )
    report = "\n".join(lines)
    with ALERTS.open("a") as f:
        f.write(report + "\n\n")
    print(report)


if __name__ == "__main__":
    main(sys.argv[1])
